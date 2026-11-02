import sys, json, threading, http.server, socketserver
sys.path.insert(0, "/home/claude/stubs")
sys.path.insert(0, "/home/claude/geocar")

from geocar_suite.core.sources import wfs, semas_pa, router

print("=== máscara CPF/CNPJ (a causa do bug da busca por CPF) ===")
print("  CPF :", semas_pa._mascarar("10636030300"))
print("  CNPJ:", semas_pa._mascarar("12345678000190"))
assert semas_pa._mascarar("10636030300") == "106.360.303-00"

print("\n=== token ===")
print("  inicial:", semas_pa.status_token())
ok, msg = semas_pa.definir_token("Bearer " + "x"*60)
print("  salvar c/ prefixo Bearer:", ok, msg)
assert ok
print("  status:", semas_pa.status_token()["tem_token"])
ok2, msg2 = semas_pa.definir_token("curto")
print("  token curto rejeitado:", not ok2, "|", msg2)
semas_pa.limpar_token()
assert not semas_pa.status_token()["tem_token"]
print("  limpo OK")

print("\n=== roteador: CPF fora do PA ===")
ok, msg = router.buscar_por_cpf("12345678901", uf="MT")
print("  MT ->", "recusou" if not ok else "aceitou")
print("  motivo (1ª linha):", msg.split("\n")[0])
assert not ok

print("\n=== WFS contra servidor falso ===")
GEOM = {"type":"Polygon","coordinates":[[[-52,-2],[-51.99,-2],[-51.99,-2.01],[-52,-2.01],[-52,-2]]]}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        cql = q.get("CQL_FILTER",[""])[0]
        self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers()
        if "NAOEXISTE" in cql:
            self.wfile.write(b'{"features":[]}'); return
        # campos reais da camada sicar_imoveis_<uf>, conferidos em
        # consulta ao vivo (out/2026): area e status_imovel — NAO
        # num_area/ind_status, que a v1 usava e nao existem.
        feat = {"type":"Feature","geometry":GEOM,"properties":{
            "cod_imovel":"PA-1501402-ABC","area":622.45,
            "municipio":"Altamira","status_imovel":"AT",
            "condicao":"Em análise","tipo_imovel":"IRU"}}
        self.wfile.write(json.dumps({"features":[feat]}).encode())

srv = socketserver.TCPServer(("127.0.0.1",0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
wfs.BASE_WFS = f"http://127.0.0.1:{srv.server_address[1]}/ows"

ok, d = wfs.buscar_imovel("PA-1501402-ABC")
print("  ok:", ok)
print("  municipio:", d["municipio"], "| area:", d["area_ha"], "| situacao:", d["situacao"], "| condicao:", d["condicao"])
print("  fonte:", d["fonte"], "| tem geometria:", bool(d["geojson"]["geometry"]))
assert ok and d["situacao"] == "Ativo" and d["area_ha"] == 622.45 and d["responsavel"] == ""

ok2, msg = wfs.buscar_imovel("PA-1501402-NAOEXISTE")
print("  inexistente ->", "erro claro" if not ok2 else "???", "|", msg[:60])
assert not ok2

print("\n=== roteador usa WFS sem token ===")
ok3, d3 = router.buscar_imovel("PA-1501402-ABC")
print("  PA sem token ->", ok3, "| fonte:", d3.get("fonte") if ok3 else d3)
assert ok3

ok4, msg4 = router.buscar_imovel("XX-1-ABC")
print("  código inválido barrado antes da rede:", not ok4)
assert not ok4

print("\n=== listagem por município ===")
ok5, lista = wfs.listar_por_municipio("PA", "Altamira")
print("  ->", ok5, "| itens:", len(lista) if ok5 else lista)
assert ok5

srv.shutdown()
print("\n✓ fontes e roteador OK")
