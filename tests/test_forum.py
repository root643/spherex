"""Pruebas del almacen del foro (solo biblioteca estandar, sin FastAPI ni red).
Ejecutar:  python3 tests/test_forum.py
"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server", "backend"))
_tmp_test_dir = tempfile.mkdtemp(prefix="forum-")
os.environ["SPHEREX_FORUM_DIR"] = _tmp_test_dir
import forum_store as fs
fs.FORUM_DIR = _tmp_test_dir

VIEW = {"ra": 269.4521, "dec": 4.6934, "fov": 240, "band": "D2", "mode": "blink", "stretch": "asinh",
        "invert": False, "speed": 500, "ref": 0, "common": True, "cross": True, "vmin": 1.0, "vmax": 9.0}
SPEC = json.dumps({"n_exposures": 90, "diagnostics": {"h2o_ice_3.05um_depth": 0.12, "co2_4.27um_depth": None},
                   "channels": [], "points": []}).encode()


def expect(status, fn, *a, **k):
    try:
        fn(*a, **k)
    except fs.ForumError as e:
        assert e.status == status, (e.status, e.message)
        return e
    raise AssertionError(f"esperaba error {status}")


def post(title="Posible objeto con movimiento", ip="1.2.3.4", now=None, **extra):
    data = {"title": title, "view": dict(VIEW), "object": {"ra": 269.45, "dec": 4.70}, **extra}
    return fs.create_post(data, ip, now)


def test_create_render_and_list():
    pid, owner = post(body="Línea 1\r\n\r\n\r\n\r\nLínea 2   \n", author="  ana  ", target="Barnard's Star")
    r = fs.get_post(pid)
    assert r["status"] == "processing" and r["gif"] is None and r["owner"] is False
    assert r["body"] == "Línea 1\n\nLínea 2" and r["author"] == "ana"
    assert r["view"]["band"] == "D2" and r["object"] == {"ra": 269.45, "dec": 4.7}
    seen = {}
    def gif(view, obj):
        seen.update(view=view, obj=obj)
        return b"GIF89a-test", 4
    fs.render(pid, gif, lambda ra, dec: SPEC)
    assert seen["obj"] == (269.45, 4.7) and seen["view"]["vmin"] == 1.0
    r = fs.get_post(pid, owner_tokens=[owner])
    assert r["status"] == "ready" and r["frames"] == 4 and r["n_exposures"] == 90 and r["owner"] is True
    assert r["diagnostics"]["h2o_ice_3.05um_depth"] == 0.12
    assert r["gif"].endswith(f"/{pid}.gif") and r["spectrum"].endswith(f"/{pid}.json")
    assert open(fs.media_path(pid, "gif"), "rb").read() == b"GIF89a-test"
    assert json.load(open(fs.media_path(pid, "json")))["n_exposures"] == 90
    fs.render(pid, gif, lambda ra, dec: SPEC)        # repetir no hace nada
    assert any(p["id"] == pid for p in fs.list_posts("new")["posts"])


def test_spectrum_failure_keeps_post_and_gif_failure_fails():
    pid, _ = post(ip="9.9.9.1")
    def bad(ra, dec):
        raise RuntimeError("IRSA no responde")
    fs.render(pid, lambda v, o: (b"GIF89a", 2), bad)
    r = fs.get_post(pid)
    assert r["status"] == "ready" and r["spectrum"] is None and "IRSA" in r["error"]
    pid2, _ = post(ip="9.9.9.2")
    class E(Exception):
        detail = "No hay imagenes validas en D2"
    def badgif(v, o):
        raise E()
    fs.render(pid2, badgif, lambda ra, dec: SPEC)
    r = fs.get_post(pid2)
    assert r["status"] == "failed" and r["error"] == "No hay imagenes validas en D2" and r["gif"] is None
    assert all(p["id"] != pid2 for p in fs.list_posts("new", limit=50)["posts"])   # las fallidas no salen


def test_validation():
    expect(422, post, title="ab")
    expect(422, fs.create_post, {"title": "Titulo valido", "view": {**VIEW, "band": "D9"}}, "5.5.5.5")
    expect(422, fs.create_post, {"title": "Titulo valido", "view": {**VIEW, "mode": "x"}}, "5.5.5.5")
    expect(422, fs.create_post, {"title": "Titulo valido", "view": {**VIEW, "dec": 91}}, "5.5.5.5")
    expect(422, fs.create_post, {"title": "Titulo valido", "view": {**VIEW, "ra": "nan"}}, "5.5.5.5")
    expect(422, fs.create_post, {"title": "Titulo valido"}, "5.5.5.5")
    pid, _ = fs.create_post({"title": "x" * 500 + "\x00\x07", "view": {**VIEW, "fov": 30, "band": "ch102",
                             "vmin": 5, "vmax": 1}, "object": None}, "5.5.5.6")
    r = fs.get_post(pid)
    assert len(r["title"]) == 120 and r["object"] is None
    assert r["view"]["fov"] == 60 and r["view"]["vmin"] is None        # fov ajustado; vmax < vmin se ignora
    expect(422, fs.create_post, {"title": "Titulo valido", "view": {**VIEW, "fov": 0}}, "5.5.5.7")


def test_rate_limit():
    now = time.time()
    for i in range(fs.POSTS_PER_HOUR):
        post(ip="7.7.7.7", now=now)
    e = expect(429, post, ip="7.7.7.7", now=now)
    assert "hora" in e.message
    post(ip="7.7.7.7", now=now + 3700)               # pasada la hora vuelve a dejar


def ready_post(ip):
    pid, owner = post(ip=ip)
    fs.render(pid, lambda v, o: (b"GIF89a", 3), lambda ra, dec: SPEC)
    return pid, owner


def test_votes():
    pid, _ = ready_post("8.8.8.1")
    assert fs.vote(pid, "voterAAAA1", 1, "10.0.0.1")["score"] == 1
    assert fs.vote(pid, "voterAAAA1", 1, "10.0.0.1")["score"] == 1          # repetir no suma
    assert fs.vote(pid, "voterAAAA1", -1, "10.0.0.1") == {"id": pid, "ups": 0, "downs": 1, "score": -1, "my_vote": -1}
    assert fs.vote(pid, "voterBBBB2", 1, "20.0.0.1")["score"] == 0
    assert fs.vote(pid, "voterAAAA1", 0, "10.0.0.1")["score"] == 1          # quitar el voto
    r = fs.get_post(pid, voter="voterBBBB2")
    assert r["my_vote"] == 1 and r["ups"] == 1 and r["downs"] == 0
    assert fs.get_post(pid, voter="otroVotante")["my_vote"] == 0
    expect(400, fs.vote, pid, "x", 1, "1.1.1.1")
    expect(422, fs.vote, pid, "voterCCCC3", 5, "1.1.1.1")
    expect(404, fs.vote, 999999, "voterCCCC3", 1, "1.1.1.1")
    pending, _ = post(ip="8.8.8.2")
    expect(409, fs.vote, pending, "voterCCCC3", 1, "1.1.1.1")
    # muchos votantes "distintos" desde la misma red /24
    for i in range(fs.VOTERS_PER_NET):
        fs.vote(pid, f"sockpuppet{i}", 1, f"30.0.0.{i + 1}")
    expect(429, fs.vote, pid, "sockpuppetX", 1, "30.0.0.200")
    fs.vote(pid, "sockpuppet0", -1, "30.0.0.1")                              # cambiar el suyo si puede
    fs.vote(pid, "voterDDDD4", 1, "31.0.0.1")                                # otra red si


def test_sorting():
    t0 = time.time()
    a, _ = ready_post("40.0.0.1")
    b, _ = ready_post("40.0.1.1")
    c, _ = ready_post("40.0.2.1")
    for i in range(4):
        fs.vote(a, f"votaA{i:05d}", 1, f"50.0.{i}.1")
    fs.vote(c, "votaC00000", -1, "60.0.0.1")
    top = [p["id"] for p in fs.list_posts("top", limit=50)["posts"]]
    assert top.index(a) < top.index(b) < top.index(c)
    new = [p["id"] for p in fs.list_posts("new", limit=50)["posts"]]
    assert new.index(c) < new.index(b) < new.index(a)
    assert fs.hot_rank(10, t0) > fs.hot_rank(1, t0) > fs.hot_rank(-5, t0)
    assert fs.hot_rank(1, t0 + 86400) > fs.hot_rank(50, t0)                 # lo nuevo acaba ganando
    page1 = fs.list_posts("hot", limit=2)
    page2 = fs.list_posts("hot", limit=2, offset=2)
    assert page1["more"] and not set(p["id"] for p in page1["posts"]) & set(p["id"] for p in page2["posts"])


def test_stale_processing():
    pid, _ = post(ip="70.0.0.1", now=time.time() - fs.STALE_SECONDS - 5)
    r = fs.get_post(pid)
    assert r["status"] == "failed" and "interrumpi" in r["error"]
    assert all(p["id"] != pid for p in fs.list_posts("new", limit=50)["posts"])


def test_delete():
    pid, owner = ready_post("80.0.0.1")
    fs.vote(pid, "voterEEEE5", 1, "81.0.0.1")
    expect(403, fs.delete_post, pid, "token-falso")
    expect(403, fs.delete_post, pid, "")
    fs.delete_post(pid, owner)
    assert not os.path.exists(fs.media_path(pid, "gif"))
    expect(404, fs.get_post, pid)
    expect(404, fs.delete_post, pid, owner)


def test_net_of():
    assert fs.net_of("192.168.1.77") == "192.168.1"
    assert fs.net_of("2001:db8:1:2:3:4:5:6") == "2001:db8:1:2"
    assert fs.hash_id("a") == fs.hash_id("a") != fs.hash_id("b")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)
    print("todo correcto")
