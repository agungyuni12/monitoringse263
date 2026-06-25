"""
Scrape jumlah UB, UM, UMK, Keluarga dari FASIH SE2026
Jalankan: python fasih_type_count.py
"""

import os, sys, json, requests
from bs4 import BeautifulSoup

FASIH_USER = os.getenv("FASIH_USER", "agung.yuniarta")
FASIH_PASS = os.getenv("FASIH_PASS", "kelayu1998")

BASE_URL  = "https://fasih-sm.bps.go.id"
SURVEY_ID = "a0429e96-51a5-477b-a415-485f9c153004"
PERIOD_ID = "fd68e454-ba45-4b85-8205-f3bf777ded24"
PENCACAH_ROLE_ID = "6d7d919a-45e5-4779-bb87-2905b49fd31a"
DOMPU_REGION2_ID = "546a26bf-e388-41ab-9083-e02cbbc093d4"

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

def login():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    resp = s.get(f"{BASE_URL}/oauth2/authorization/ics", timeout=30)
    soup = BeautifulSoup(resp.text, "html.parser")
    form = soup.find("form", {"id": "kc-form-login"})
    if not form:
        sys.exit("ERROR: form login tidak ditemukan")
    action = form["action"]
    data = {i["name"]: i.get("value", "") for i in form.find_all("input") if i.get("name")}
    data["username"] = FASIH_USER
    data["password"] = FASIH_PASS
    s.post(action, data=data, headers={"Origin": "https://sso.bps.go.id", "Referer": resp.url}, timeout=30)
    xsrf = s.cookies.get("XSRF-TOKEN")
    if not xsrf:
        sys.exit("ERROR: XSRF-TOKEN tidak ditemukan setelah login")
    print(f"[LOGIN] OK, XSRF={xsrf[:20]}...")
    return s, xsrf

def post_api(s, xsrf, endpoint, payload):
    headers = {
        "Content-Type": "application/json",
        "X-XSRF-TOKEN": xsrf,
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/app/surveys/{SURVEY_ID}/{PERIOD_ID}",
        "Origin": BASE_URL,
    }
    resp = s.post(f"{BASE_URL}{endpoint}", json=payload, headers=headers, timeout=30)
    return resp

REGION = {
    "region1Id": None, "region2Id": DOMPU_REGION2_ID,
    "region3Id": None, "region4Id": None, "region5Id": None,
    "region6Id": None, "region7Id": None, "region8Id": None,
    "region9Id": None, "region10Id": None,
}

def try_type_summary(s, xsrf):
    """Coba endpoint summary per target type."""
    endpoints = [
        "/analytic/api/v2/assignment/report-progress-by-target-type",
        "/analytic/api/v2/assignment/report-progress-by-type",
        "/analytic/api/v2/assignment/target-type-summary",
    ]
    base_payload = {
        "surveyPeriodId": PERIOD_ID,
        "region": REGION,
        "target": "TARGET_ONLY",
    }
    for ep in endpoints:
        print(f"\n[TRY] {ep}")
        try:
            r = post_api(s, xsrf, ep, base_payload)
            print(f"  Status: {r.status_code}")
            print(f"  Body  : {r.text[:500]}")
        except Exception as e:
            print(f"  Error: {e}")

def try_progress_with_type_filter(s, xsrf):
    """Coba progress endpoint dengan filter targetType per jenis."""
    types_to_try = ["UB", "UM", "UMK", "KELUARGA", "UNT"]
    ep = "/analytic/api/v2/assignment/report-progress-by-responsibility"

    print("\n=== Coba filter targetType di progress API ===")
    for t in types_to_try:
        payload = {
            "surveyPeriodId": PERIOD_ID,
            "surveyRoleId": PENCACAH_ROLE_ID,
            "target": "TARGET_ONLY",
            "region": REGION,
            "regionSummaryLevel": 6,
            "targetType": t,
            "page": 0,
            "size": 1,
            "search": "",
        }
        try:
            r = post_api(s, xsrf, ep, payload)
            data = r.json()
            total = data.get("data", {}).get("totalElements")
            print(f"  {t:12s}: totalElements={total}  (status={r.status_code})")
        except Exception as e:
            print(f"  {t:12s}: Error — {e}")

def try_data_list(s, xsrf):
    """Coba endpoint data listing dengan filter tipe."""
    print("\n=== Coba data listing API ===")
    candidate_endpoints = [
        "/survey/api/v1/data/assignment/list",
        "/survey/api/v2/data/list",
        "/analytic/api/v2/data/list",
        f"/survey/api/v1/survey/{SURVEY_ID}/period/{PERIOD_ID}/data",
    ]
    payload = {"page": 0, "size": 1, "surveyPeriodId": PERIOD_ID}
    for ep in candidate_endpoints:
        try:
            r = post_api(s, xsrf, ep, payload)
            print(f"  {ep}: status={r.status_code}, body={r.text[:200]}")
        except Exception as e:
            print(f"  {ep}: {e}")

def try_assignment_count(s, xsrf):
    """GET /analytic/api/v2/assignment/count untuk cek total per tipe."""
    print("\n=== Coba GET count endpoint ===")
    params = f"?surveyPeriodId={PERIOD_ID}&region2Id={DOMPU_REGION2_ID}"
    candidates = [
        f"/analytic/api/v2/assignment/count{params}",
        f"/analytic/api/v2/assignment/summary{params}",
    ]
    headers = {
        "X-XSRF-TOKEN": xsrf,
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/app/surveys/{SURVEY_ID}/{PERIOD_ID}",
    }
    for ep in candidates:
        try:
            r = s.get(f"{BASE_URL}{ep}", headers=headers, timeout=15)
            print(f"  {ep}: status={r.status_code}, body={r.text[:300]}")
        except Exception as e:
            print(f"  {ep}: {e}")

def dump_raw_progress(s, xsrf):
    """Print full JSON dari 1 pencacah untuk lihat semua field yang ada."""
    ep = "/analytic/api/v2/assignment/report-progress-by-responsibility"
    payload = {
        "surveyPeriodId": PERIOD_ID,
        "surveyRoleId": PENCACAH_ROLE_ID,
        "target": "TARGET_ONLY",
        "region": REGION,
        "regionSummaryLevel": 6,
        "page": 0,
        "size": 1,
        "search": "",
    }
    r = post_api(s, xsrf, ep, payload)
    print("\n=== RAW RESPONSE (1 pencacah, truncated 3000 chars) ===")
    print(r.text[:3000])

def try_data_endpoints(s, xsrf):
    """Coba endpoint data individual assignment."""
    print("\n=== Coba endpoint data listing lainnya ===")
    headers = {
        "Content-Type": "application/json",
        "X-XSRF-TOKEN": xsrf,
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/app/surveys/{SURVEY_ID}/{PERIOD_ID}",
        "Origin": BASE_URL,
    }
    candidates = [
        ("POST", "/analytic/api/v2/assignment/list", {"surveyPeriodId": PERIOD_ID, "page": 0, "size": 1}),
        ("POST", "/analytic/api/v2/assignment/data/list", {"surveyPeriodId": PERIOD_ID, "page": 0, "size": 1}),
        ("GET",  f"/analytic/api/v2/assignment?surveyPeriodId={PERIOD_ID}&page=0&size=1", None),
        ("POST", "/survey/api/v1/survey-period/assignment/list", {"surveyPeriodId": PERIOD_ID, "page": 0, "size": 1}),
        ("POST", "/analytic/api/v2/respondent/list", {"surveyPeriodId": PERIOD_ID, "page": 0, "size": 1}),
        ("POST", f"/survey/api/v1/data", {"surveyPeriodId": PERIOD_ID, "page": 0, "size": 1}),
    ]
    for method, ep, body in candidates:
        try:
            if method == "GET":
                r = s.get(f"{BASE_URL}{ep}", headers=headers, timeout=15)
            else:
                r = s.post(f"{BASE_URL}{ep}", json=body, headers=headers, timeout=15)
            snippet = r.text[:200].replace("\n", " ")
            print(f"  [{method}] {ep}: {r.status_code} → {snippet}")
        except Exception as e:
            print(f"  [{method}] {ep}: ERROR {e}")

def try_swagger(s, xsrf):
    """Cek apakah ada Swagger/API docs yang bisa kasih tahu endpoint."""
    print("\n=== Coba Swagger / API docs ===")
    paths = ["/swagger-ui.html", "/v3/api-docs", "/api-docs", "/actuator/mappings",
             "/survey/v3/api-docs", "/analytic/v3/api-docs"]
    for p in paths:
        try:
            r = s.get(f"{BASE_URL}{p}", timeout=10)
            print(f"  {p}: {r.status_code} ({len(r.text)} chars)")
            if r.status_code == 200:
                print(f"    → {r.text[:300]}")
        except Exception as e:
            print(f"  {p}: {e}")

def try_list_with_full_params(s, xsrf):
    """Coba /assignment/list dengan payload lengkap."""
    print("\n=== Coba /assignment/list dengan payload lengkap ===")
    headers = {
        "Content-Type": "application/json",
        "X-XSRF-TOKEN": xsrf,
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/app/surveys/{SURVEY_ID}/{PERIOD_ID}",
        "Origin": BASE_URL,
    }
    payloads = [
        {  # versi dengan surveyRoleId dan region
            "surveyPeriodId": PERIOD_ID,
            "surveyRoleId": PENCACAH_ROLE_ID,
            "region": REGION,
            "target": "TARGET_ONLY",
            "page": 0, "size": 1, "search": "",
        },
        {  # versi minimal dengan regionCode
            "surveyPeriodId": PERIOD_ID,
            "regionCode": "5205",
            "page": 0, "size": 1,
        },
        {  # versi dengan surveyId
            "surveyId": SURVEY_ID,
            "surveyPeriodId": PERIOD_ID,
            "page": 0, "size": 1,
        },
    ]
    endpoints = [
        "/analytic/api/v2/assignment/list",
        "/analytic/api/v2/assignment/respondent/list",
        "/analytic/api/v2/data",
        "/survey/api/v1/assignment",
    ]
    for ep in endpoints:
        for i, payload in enumerate(payloads):
            try:
                r = s.post(f"{BASE_URL}{ep}", json=payload, headers=headers, timeout=15)
                if r.status_code != 500:
                    print(f"  {ep} [payload#{i}]: {r.status_code} → {r.text[:300]}")
                else:
                    print(f"  {ep} [payload#{i}]: 500")
            except Exception as e:
                print(f"  {ep} [payload#{i}]: {e}")

def fetch_app_page(s, xsrf):
    """Fetch halaman app FASIH dan cari URL API di JS bundle."""
    print("\n=== Cari API endpoint di halaman app ===")
    url = f"{BASE_URL}/app/surveys/{SURVEY_ID}/{PERIOD_ID}"
    r = s.get(url, timeout=15)
    print(f"  Status: {r.status_code}")
    # Cari referensi ke /api/ dalam HTML
    import re
    apis = re.findall(r'["\']([^"\']*?/api/[^"\']*?)["\']', r.text)
    apis_unique = list(dict.fromkeys(apis))[:20]
    if apis_unique:
        print("  API patterns ditemukan di HTML:")
        for a in apis_unique:
            print(f"    {a}")
    # Cari JS bundle URLs
    js_urls = re.findall(r'src="([^"]*\.js[^"]*)"', r.text)
    print(f"\n  JS bundles: {js_urls[:5]}")
    # Fetch satu JS bundle dan cari pattern API
    for js_url in js_urls[:2]:
        full = js_url if js_url.startswith("http") else BASE_URL + js_url
        try:
            jr = s.get(full, timeout=20)
            matches = re.findall(r'["\`](/(?:analytic|survey|data|entry)[^"\`\s,]{5,80})["\`]', jr.text)
            unique_matches = list(dict.fromkeys(matches))[:30]
            if unique_matches:
                print(f"\n  Dari {js_url} — endpoint patterns:")
                for m in unique_matches:
                    print(f"    {m}")
        except Exception as e:
            print(f"  Error fetch JS: {e}")

def try_datatable(s, xsrf):
    """Coba endpoint datatable yang ditemukan di JS bundle."""
    print("\n=== Coba datatable endpoints ===")
    headers = {
        "Content-Type": "application/json",
        "X-XSRF-TOKEN": xsrf,
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/app/surveys/{SURVEY_ID}/{PERIOD_ID}",
        "Origin": BASE_URL,
    }
    ep = "/analytic/api/v2/assignment/datatable-all-user-survey-periode"
    payloads = [
        {"surveyPeriodId": PERIOD_ID, "page": 0, "size": 1},
        {"surveyPeriodId": PERIOD_ID, "surveyRoleId": PENCACAH_ROLE_ID, "region": REGION, "page": 0, "size": 1},
        {"surveyPeriodId": PERIOD_ID, "region": REGION, "page": 0, "size": 5, "draw": 1, "start": 0, "length": 5},
        {
            "surveyPeriodId": PERIOD_ID,
            "surveyRoleId": PENCACAH_ROLE_ID,
            "region": REGION,
            "target": "TARGET_ONLY",
            "page": 0,
            "size": 5,
            "search": "",
        },
    ]
    for i, payload in enumerate(payloads):
        r = s.post(f"{BASE_URL}{ep}", json=payload, headers=headers, timeout=20)
        print(f"\n  payload#{i}: status={r.status_code}")
        print(f"  {r.text[:600]}")

def fetch_more_js_endpoints(s, xsrf):
    """Ambil lebih banyak endpoint dari JS bundle."""
    import re
    print("\n=== Semua endpoint dari JS bundle ===")
    r = s.get(f"{BASE_URL}/app/assets/index-BGdCZ8Yo.js", timeout=30)
    # Cari semua path yang ada 'analytic' atau 'survey' dengan keyword data/assignment
    patterns = re.findall(
        r'["\`](/(?:analytic|survey)/api/v[12]/(?:assignment|data|respondent|target)[^"\`\s,<>]{3,100})["\`]',
        r.text
    )
    unique = list(dict.fromkeys(patterns))
    print(f"  Ditemukan {len(unique)} endpoint unik:")
    for p in unique:
        print(f"    {p}")

def try_new_endpoints(s, xsrf):
    """Coba 2 endpoint baru yang ditemukan."""
    headers = {
        "Content-Type": "application/json",
        "X-XSRF-TOKEN": xsrf,
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/app/surveys/{SURVEY_ID}/{PERIOD_ID}",
        "Origin": BASE_URL,
    }
    base_payloads = [
        {"surveyPeriodId": PERIOD_ID, "page": 0, "size": 1},
        {"surveyPeriodId": PERIOD_ID, "region": REGION, "page": 0, "size": 1},
        {"surveyPeriodId": PERIOD_ID, "surveyRoleId": PENCACAH_ROLE_ID, "region": REGION, "target": "TARGET_ONLY", "page": 0, "size": 1, "search": ""},
        {"surveyPeriodId": PERIOD_ID, "region": REGION, "regionSummaryLevel": 6, "page": 0, "size": 1},
        {"surveyPeriodId": PERIOD_ID, "surveyId": SURVEY_ID, "region": REGION, "page": 0, "size": 1},
    ]
    endpoints = [
        "/analytic/api/v2/assignment/report-progress-assignment",
        "/analytic/api/v2/assignment/report-user-assignment",
    ]
    for ep in endpoints:
        print(f"\n{'='*60}")
        print(f"  {ep}")
        for i, payload in enumerate(base_payloads):
            r = s.post(f"{BASE_URL}{ep}", json=payload, headers=headers, timeout=20)
            if r.status_code != 500:
                print(f"\n  *** payload#{i} BERHASIL: {r.status_code} ***")
                print(f"  {r.text[:1000]}")
            else:
                print(f"  payload#{i}: 500")

def search_js_for_type(s):
    """Cari kata 'target' dan 'type' di JS bundle untuk temukan param groupBy."""
    import re
    print("\n=== Cari 'targetType'/'groupBy' di JS bundle ===")
    r = s.get(f"{BASE_URL}/app/assets/index-BGdCZ8Yo.js", timeout=30)
    # Cari konteks sekitar 'targetType' atau 'groupBy'
    for keyword in ["targetType", "groupBy", "target_type", "reportType", "breakdown", "reportLevel"]:
        matches = [(m.start(), m.end()) for m in re.finditer(keyword, r.text)]
        if matches:
            print(f"\n  Keyword '{keyword}' ditemukan {len(matches)}x:")
            for start, end in matches[:3]:
                snippet = r.text[max(0,start-80):end+80].replace("\n", " ")
                print(f"    ...{snippet}...")

def try_groupby_type(s, xsrf):
    """Coba variasi parameter untuk group by target type."""
    headers = {
        "Content-Type": "application/json",
        "X-XSRF-TOKEN": xsrf,
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/app/surveys/{SURVEY_ID}/{PERIOD_ID}",
        "Origin": BASE_URL,
    }
    ep = "/analytic/api/v2/assignment/report-progress-assignment"
    print(f"\n=== Coba groupBy/filter params di {ep} ===")

    # Coba berbagai parameter grouping dan filter region
    variations = [
        {"surveyPeriodId": PERIOD_ID, "groupBy": "TARGET_TYPE", "page": 0, "size": 10},
        {"surveyPeriodId": PERIOD_ID, "groupBy": "targetType", "page": 0, "size": 10},
        {"surveyPeriodId": PERIOD_ID, "reportLevel": "TARGET_TYPE"},
        {"surveyPeriodId": PERIOD_ID, "breakdown": "targetType"},
        {"surveyPeriodId": PERIOD_ID, "region": REGION, "groupBy": "targetType"},
        # Coba filter ke Dompu dengan kode
        {"surveyPeriodId": PERIOD_ID, "regionCode": "5205"},
        {"surveyPeriodId": PERIOD_ID, "region2Id": DOMPU_REGION2_ID},
        {"surveyPeriodId": PERIOD_ID, "region": REGION},
        # Coba dengan kode kabupaten langsung
        {"surveyPeriodId": PERIOD_ID, "regionCode": "52050000"},
    ]
    for i, payload in enumerate(variations):
        r = s.post(f"{BASE_URL}{ep}", json=payload, headers=headers, timeout=20)
        result = r.text[:400].replace("\n", " ")
        print(f"\n  [{i}] payload={list(payload.keys())[2:] if len(payload)>2 else ''}: {r.status_code}")
        print(f"       {result}")

def search_js_skala(s):
    """Cari kata 'skala', 'scale', 'ub', 'umk' di JS bundle."""
    import re
    print("\n=== Cari 'skala'/'scale'/'businessScale' di JS bundle ===")
    r = s.get(f"{BASE_URL}/app/assets/index-BGdCZ8Yo.js", timeout=30)
    text = r.text
    for keyword in ["skala", "Skala", "SKALA", "businessScale", "business_scale",
                    "scaleType", "scale", '"UB"', '"UMK"', '"KELUARGA"', "usahaBesar"]:
        matches = list(re.finditer(re.escape(keyword), text))
        if matches:
            print(f"\n  '{keyword}' — {len(matches)}x:")
            for m in matches[:3]:
                snippet = text[max(0,m.start()-100):m.end()+100].replace("\n"," ")
                print(f"    ...{snippet}...")

def try_skala_filter(s, xsrf):
    """Coba filter skala usaha di endpoint yang sudah berhasil."""
    headers = {
        "Content-Type": "application/json",
        "X-XSRF-TOKEN": xsrf,
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/app/surveys/{SURVEY_ID}/{PERIOD_ID}",
        "Origin": BASE_URL,
    }
    ep = "/analytic/api/v2/assignment/report-progress-assignment"
    print(f"\n=== Coba filter skala usaha ===")
    scale_values = ["UB", "UM", "UMK", "KELUARGA", "USAHA_BESAR", "USAHA_MENENGAH",
                    "USAHA_MIKRO_KECIL", "RUMAH_TANGGA"]
    for scale in scale_values:
        for key in ["skalaUsaha", "businessScale", "scaleType", "scale", "skala"]:
            payload = {"surveyPeriodId": PERIOD_ID, key: scale}
            r = s.post(f"{BASE_URL}{ep}", json=payload, headers=headers, timeout=15)
            data = r.json()
            # Kalau hasilnya berbeda dari tanpa filter (total != 122105), berarti filter bekerja
            total = None
            if isinstance(data, list) and data:
                for v in data[0].get("values", []):
                    if v["label"] == "total":
                        total = v["value"]
            if total is not None and total != 122105:
                print(f"  *** FILTER BEKERJA: {key}={scale} → total={total} ***")
                print(f"      {r.text[:300]}")
            elif total == 122105:
                pass  # tidak berubah, skip
            else:
                print(f"  {key}={scale}: {r.status_code} → {r.text[:100]}")

if __name__ == "__main__":
    s, xsrf = login()
    search_js_skala(s)
    try_skala_filter(s, xsrf)
