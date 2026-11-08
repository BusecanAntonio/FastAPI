# hello.py: API IoT pentru citiri de la senzori + teste automate încorporate
from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

app = FastAPI(title="TPI IoT API")

DEV_PATTERN = r"^[a-z0-9-]{1,32}$"


class Reading(BaseModel):
    # dev: text - doar litere mici a-z, cifre 0-9 și cratimă -, între 1 și 32 de caractere
    dev: str = Field(pattern=DEV_PATTERN)

    # seq: întreg - număr de ordine mai mare sau egal cu 0
    seq: int = Field(ge=0)

    # ts: întreg - secunde Unix, UTC; strict după septembrie 2020 (> 1_600_000_000).
    # Un senzor cu ceasul nesetat raportează de obicei 0 (1 ianuarie 1970, epoch),
    # deci o valoare așa de mică indică o dată greșită; condiția respinge astfel de citiri.
    ts: int = Field(gt=1_600_000_000)

    # temp: număr real - valoare în grade Celsius între -40 și 85
    temp: float = Field(ge=-40, le=85)

    # hum: număr real sau null - opțional, procent de umiditate între 0 și 100
    hum: float | None = Field(default=None, ge=0, le=100)


# "Baza de date" în memorie: dev_id -> {seq: Reading}
DB: dict[str, dict[int, Reading]] = {}


@app.get("/health")
def health():
    return {"status": "ok"}


# --- POST /readings ---
@app.post("/readings", status_code=201)
def create_reading(reading: Reading) -> Reading:
    device = DB.setdefault(reading.dev, {})

    # seq deja existent pentru același dev -> 409
    if reading.seq in device:
        raise HTTPException(
            status_code=409,
            detail=f"Citirea cu seq {reading.seq} pentru dispozitivul '{reading.dev}' există deja.",
        )

    device[reading.seq] = reading
    return reading


# --- GET /devices (bonus) ---
@app.get("/devices")
def devices() -> list[dict]:
    return [{"dev": dev, "count": len(r)} for dev, r in sorted(DB.items()) if r]


# --- GET /devices/{dev_id}/readings ---
@app.get("/devices/{dev_id}/readings")
def readings(
    dev_id: str = Path(pattern=DEV_PATTERN),
    since: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    min_temp: float | None = Query(None),  # bonus: exclude citirile cu temp < min_temp
) -> list[Reading]:
    # Dispozitiv necunoscut (sau fără citiri) -> 404
    if not DB.get(dev_id):
        raise HTTPException(
            status_code=404,
            detail=f"Dispozitivul '{dev_id}' nu a fost găsit sau nu are citiri.",
        )

    result = [
        r for seq, r in sorted(DB[dev_id].items())
        if seq > since and (min_temp is None or r.temp >= min_temp)
    ]
    return result[:limit]


# =====================================================================
# TESTE (rulați cu: pytest hello.py)  -  necesită: pip install pytest httpx
# =====================================================================
client = TestClient(app)


def setup_function():
    DB.clear()


def post(seq, temp=21.5, dev="senzor-1", **extra):
    body = {"dev": dev, "seq": seq, "ts": 1762096000, "temp": temp, **extra}
    return client.post("/readings", json=body)


def test_1_salvare_corecta():
    r = post(1, hum=40)
    assert r.status_code == 201
    assert r.json()["seq"] == 1


def test_2_seq_duplicat():
    post(1, hum=40)
    r = post(1, hum=40)
    assert r.status_code == 409
    assert "detail" in r.json()


def test_3_temp_invalida():
    assert post(2, temp=100).status_code == 422


def test_4_dev_invalid():
    assert post(2, dev="Senzor 1").status_code == 422


def test_5_hum_optional():
    r = post(2)
    assert r.status_code == 201
    assert r.json()["hum"] is None


def test_6_get_readings():
    post(1)
    post(2)
    r = client.get("/devices/senzor-1/readings")
    assert r.status_code == 200
    assert [x["seq"] for x in r.json()] == [1, 2]


def test_7_get_since():
    post(1)
    post(2)
    r = client.get("/devices/senzor-1/readings?since=1")
    assert r.status_code == 200
    assert [x["seq"] for x in r.json()] == [2]


def test_8_get_limit():
    post(1)
    post(2)
    r = client.get("/devices/senzor-1/readings?limit=1")
    assert r.status_code == 200
    assert [x["seq"] for x in r.json()] == [1]


def test_9_limit_sub_1():
    post(1)
    assert client.get("/devices/senzor-1/readings?limit=0").status_code == 422


def test_10_dispozitiv_necunoscut():
    assert client.get("/devices/necunoscut/readings").status_code == 404


def test_11_dev_id_invalid():
    assert client.get("/devices/ABC/readings").status_code == 422


# --- bonusuri ---
def test_bonus_min_temp():
    post(1, temp=10)
    post(2, temp=25)
    r = client.get("/devices/senzor-1/readings?min_temp=20")
    assert [x["seq"] for x in r.json()] == [2]


def test_bonus_devices():
    post(1)
    post(2)
    post(1, dev="alt-senzor")
    r = client.get("/devices")
    assert r.status_code == 200
    assert r.json() == [{"dev": "alt-senzor", "count": 1}, {"dev": "senzor-1", "count": 2}]


# =====================================================================
# RULARE ÎN TERMINAL: `python hello.py` afișează toate testele cu codurile lor
# =====================================================================
def rulare_in_terminal():
    DB.clear()  # server "proaspăt pornit"
    base = {"dev": "senzor-1", "seq": 1, "ts": 1762096000, "temp": 21.5, "hum": 40}

    # (nr, descriere, metodă, url, json, cod așteptat)
    teste = [
        (1, "POST seq 1 (salvare corectă)", "POST", "/readings", base, 201),
        (2, "POST seq 1 din nou (duplicat)", "POST", "/readings", base, 409),
        (3, "POST seq 2, temp 100", "POST", "/readings", {**base, "seq": 2, "temp": 100}, 422),
        (4, "POST dev 'Senzor 1'", "POST", "/readings", {**base, "seq": 2, "dev": "Senzor 1"}, 422),
        (5, "POST seq 2, fără hum", "POST", "/readings",
         {"dev": "senzor-1", "seq": 2, "ts": 1762096000, "temp": 21.5}, 201),
        (6, "GET lista (seq 1 și 2)", "GET", "/devices/senzor-1/readings", None, 200),
        (7, "GET ?since=1 (doar seq 2)", "GET", "/devices/senzor-1/readings?since=1", None, 200),
        (8, "GET ?limit=1 (doar seq 1)", "GET", "/devices/senzor-1/readings?limit=1", None, 200),
        (9, "GET ?limit=0", "GET", "/devices/senzor-1/readings?limit=0", None, 422),
        (10, "GET dispozitiv necunoscut", "GET", "/devices/necunoscut/readings", None, 404),
        (11, "GET dev_id 'ABC'", "GET", "/devices/ABC/readings", None, 422),
        ("B1", "BONUS GET ?min_temp=22", "GET", "/devices/senzor-1/readings?min_temp=22", None, 200),
        ("B2", "BONUS GET /devices", "GET", "/devices", None, 200),
    ]

    print("=" * 78)
    print(f"{'#':<4}{'Cerere':<42}{'Așteptat':<10}{'Primit':<8}Rezultat")
    print("=" * 78)

    trecute = 0
    for nr, descriere, metoda, url, corp, asteptat in teste:
        r = client.request(metoda, url, json=corp)
        ok = r.status_code == asteptat
        trecute += ok
        print(f"{nr!s:<4}{(metoda + ' ' + url)[:40]:<42}{asteptat:<10}{r.status_code:<8}"
              f"{'PASS' if ok else 'FAIL'}")
        print(f"    -> {descriere}")
        print(f"    -> răspuns: {r.text}")
        print("-" * 78)

    print(f"Total: {trecute}/{len(teste)} teste trecute")


if __name__ == "__main__":
    rulare_in_terminal()