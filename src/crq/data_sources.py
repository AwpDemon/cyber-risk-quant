"""Download the public datasets used for calibration and validation.

All raw files land in ``data/raw/`` (git-ignored). Only small, derived,
non-identifying tables are committed (``data/processed/``) plus the KEV lag
table shipped inside the package so the model runs offline.

| Source | Licence / terms |
|---|---|
| CISA Known Exploited Vulnerabilities catalog | U.S. Government work (public domain in the U.S.) |
| NVD CVE API 2.0 (``hasKev`` filter) | Public domain; "This product uses data from the NVD API but is not endorsed or certified by the NVD." |
| HHS OCR Breach Portal (HIPAA breaches >= 500 individuals) | U.S. Government work (public domain in the U.S.) |
| VERIS Community Database (VCDB) | CC BY-SA 4.0 (attribution + share-alike for derived data) |
| Census County Business Patterns 2022 (U.S. file) | U.S. Government work (public domain in the U.S.) |
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from pathlib import Path

import requests

USER_AGENT = "AwpDemon portfolio aliaskari.georgia@gmail.com"
REPO_ROOT = Path(__file__).resolve().parents[2]
RAW = REPO_ROOT / "data" / "raw"

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
HHS_BASE = "https://ocrportal.hhs.gov/ocr/breach/"
VCDB_URL = "https://github.com/vz-risk/VCDB/raw/master/data/csv/vcdb.csv.zip"
CBP_URL = "https://www2.census.gov/programs-surveys/cbp/datasets/2022/cbp22us.zip"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _save(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    print(f"  saved {path.relative_to(REPO_ROOT)} ({len(content) / 1e6:.1f} MB)")
    return path


def download_kev(s: requests.Session) -> Path:
    r = s.get(KEV_URL, timeout=60)
    r.raise_for_status()
    return _save(RAW / "kev.json", r.content)


def download_nvd_kev(s: requests.Session) -> Path:
    """All CVEs in KEV with their NVD publication dates (paged, 2000 per page).

    Without an API key NVD allows 5 requests / 30 s; we sleep 6.5 s between pages.
    """
    pages, start = [], 0
    while True:
        r = s.get(NVD_URL, params={"hasKev": "", "resultsPerPage": 2000, "startIndex": start}, timeout=120)
        r.raise_for_status()
        js = r.json()
        pages.append(r.text)
        start += js["resultsPerPage"]
        if start >= js["totalResults"] or js["resultsPerPage"] == 0:
            break
        time.sleep(6.5)
    merged = json.loads(pages[0])
    for extra in pages[1:]:
        merged["vulnerabilities"].extend(json.loads(extra)["vulnerabilities"])
    return _save(RAW / "nvd_kev.json", json.dumps(merged).encode("utf-8"))


def _viewstate(html: str) -> str:
    return re.search(r'name="javax.faces.ViewState"[^>]*value="([^"]+)"', html).group(1)


def _multipart(d: dict[str, str]) -> dict:
    return {k: (None, v) for k, v in d.items()}


def download_hhs(s: requests.Session) -> list[Path]:
    """Export both tabs of the HHS OCR breach portal ("under investigation" + "archive") as CSV.

    The portal is a JSF/PrimeFaces app with no API, so we replay the same form
    posts a browser makes: open the report page, (for the archive) switch tab
    via the PrimeFaces AJAX ``tabChange`` event, then click the CSV export icon.
    """
    out = []
    front = s.get(HHS_BASE + "breach_frontpage.jsf", timeout=60)
    link = next(i for i, label in re.findall(r"\{'(ocrForm:j_idt\d+)'[^>]*>([^<]*)<", front.text)
                if label == "View HIPAA Breach Reports")
    page = s.post(HHS_BASE + "breach_frontpage.jsf",
                  files=_multipart({"ocrForm": "ocrForm", "javax.faces.ViewState": _viewstate(front.text), link: link}), timeout=60)
    tab_id = re.search(r'id="(ocrForm:j_idt\d+)"[^>]*class="ui-tabs', page.text).group(1)
    csv_re = r"\{'(ocrForm:[^']+)'[^>]*><img id=\"[^\"]+\" src=\"/ocr/images/icons/csv"

    # Tab 0: breaches reported in the last 24 months (still under investigation)
    btn = re.findall(csv_re, page.text)[0]
    r = s.post(HHS_BASE + "breach_report_hip.jsf",
               files=_multipart({"ocrForm": "ocrForm", "javax.faces.ViewState": _viewstate(page.text), btn: btn}), timeout=120)
    r.raise_for_status()
    out.append(_save(RAW / "hhs_breaches_open.csv", r.content))
    time.sleep(2)

    # Tab 1: archive (closed investigations, back to 2009)
    vs = _viewstate(page.text)
    ajax = {
        "javax.faces.partial.ajax": "true", "javax.faces.source": tab_id, "javax.faces.partial.execute": tab_id,
        "javax.faces.partial.render": "ocrForm:breachReports ocrForm:results", "javax.faces.behavior.event": "tabChange",
        "javax.faces.partial.event": "tabChange", f"{tab_id}_newTab": f"{tab_id}:archiveTab", f"{tab_id}_tabindex": "1",
        "ocrForm": "ocrForm", f"{tab_id}_activeIndex": "1", "javax.faces.ViewState": vs,
    }
    t = s.post(HHS_BASE + "breach_report_hip.jsf", data=ajax, timeout=120,
               headers={"Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest"})
    m = re.search(r"ViewState[^>]*>\s*<!\[CDATA\[([^\]]+)", t.text)
    vs = m.group(1) if m else vs
    btn = re.findall(csv_re, t.text)[0]
    time.sleep(2)
    r = s.post(HHS_BASE + "breach_report_hip.jsf",
               files=_multipart({"ocrForm": "ocrForm", "javax.faces.ViewState": vs, btn: btn, f"{tab_id}_activeIndex": "1"}), timeout=300)
    r.raise_for_status()
    out.append(_save(RAW / "hhs_breaches_archive.csv", r.content))
    return out


def download_vcdb(s: requests.Session) -> Path:
    r = s.get(VCDB_URL, timeout=300)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = next(n for n in z.namelist() if n.endswith("vcdb.csv"))
        return _save(RAW / "vcdb.csv", z.read(name))


def download_cbp(s: requests.Session) -> Path:
    r = s.get(CBP_URL, timeout=300)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        return _save(RAW / "cbp22us.txt", z.read(z.namelist()[0]))


DOWNLOADERS = {
    "kev": download_kev,
    "nvd": download_nvd_kev,
    "hhs": download_hhs,
    "vcdb": download_vcdb,
    "cbp": download_cbp,
}


def download_all(which: list[str] | None = None) -> None:
    s = _session()
    for name in which or list(DOWNLOADERS):
        print(f"[{name}]")
        DOWNLOADERS[name](s)
        time.sleep(1)
