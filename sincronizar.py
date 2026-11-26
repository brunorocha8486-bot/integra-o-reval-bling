import os
import json
import base64
import time
import requests

REVAL_BASE = "https://" + "api.reval.net/api"
BLING_BASE = "https://" + "api.bling.com.br/Api/v3"
RAW_GITHUB_BASE = "https://" + "brunorocha8486-bot/integra-o-reval-bling/fotos"

REVAL_USER = os.environ.get("REVAL_USER")
REVAL_PASS = os.environ.get("REVAL_PASS")
BLING_CLIENT_ID = os.environ.get("BLING_CLIENT_ID")
BLING_CLIENT_SECRET = os.environ.get("BLING_CLIENT_SECRET")
BLING_AUTH_CODE = os.environ.get("BLING_AUTH_CODE", "")

TOKEN_FILE = "bling_tokens.json"
FOTOS_DIR = "fotos"
SALDO_DISPONIVEL = 5
MARGEM_VENDA = 1.65


def parse_preco(valor):
    if valor is None:
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    txt = str(valor).replace("R$", "").replace(".", "").replace(",", ".").strip()
    try:
        return float(txt)
    except Exception:
        return 0.0


def get_bling_token():
    auth_str = f"{BLING_CLIENT_ID}:{BLING_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    headers = {
        "Authorization": f"Basic {b64_auth}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "1.0"
    }

    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            tokens = json.load(f)
        data = {
            "grant_type": "refresh_token",
            "refresh_token": tokens.get("refresh_token")
        }
        r = requests.post(f"{BLING_BASE}/oauth/token", headers=headers, data=data)
        if r.status_code == 200:
            new_tokens = r.json()
            with open(TOKEN_FILE, "w") as f:
                json.dump(new_tokens, f)
            return new_tokens.get("access_token")

    data = {
        "grant_type": "authorization_code",
        "code": BLING_AUTH_CODE
    }
    r = requests.post(f"{BLING_BASE}/oauth/token", headers=headers, data=data)
    if r.status_code != 200:
        raise Exception(f"Falha ao obter token inicial do Bling: {r.text}")

    new_tokens = r.json()
    with open(TOKEN_FILE, "w") as f:
        json.dump(new_tokens, f)
    return new_tokens.get("access_token")


def get_reval_token():
    r = requests.get(
        f"{REVAL_BASE}/get-token",
        params={"username": REVAL_USER, "password": REVAL_PASS}
    )
    if r.status_code != 200:
        raise Exception(f"Erro ao autenticar na Reval: {r.text}")
    data = r.json()
    if isinstance(data, str):
        return data
    return data.get("token") or data.get("access_token") or str(data)


def get_reval_products(reval_token):
    headers = {"Authorization": f"Bearer {reval_token}", "Accept": "application/json"}
    produtos = []
    for pagina in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
        r = requests.get(
            f"{REVAL_BASE}/produto/get-page-tabela",
            headers=headers,
            params={"usuario": REVAL_USER, "pageIndex": pagina, "pageSize": 100}
        )
        if r.status_code != 200:
            break
        resp = r.json()
        lista = resp.get("models") if isinstance(resp, dict) else resp
        if not lista:
            break
        produtos.extend(lista)
    return produtos


def baixar_fotos_reval(reval_token, sku):
    os.makedirs(FOTOS_DIR, exist_ok=True)
    headers_rev = {"Authorization": f"Bearer {reval_token}", "Accept": "application/json"}
    externas = []
    try:
        r = requests.get(
            f"{REVAL_BASE}/imagem/get-all-imagem",
            headers=headers_rev,
            params={"produto": sku},
            timeout=10
        )
        if r.status_code == 200:
            dados = r.json()
            lista = dados if isinstance(dados, list) else (dados.get("models") or dados.get("data") or [])
            for idx, img in enumerate(lista[:6]):
                if not isinstance(img, dict):
                    continue
                b64_data = img.get("arquivo")
                if not b64_data:
                    continue
                nome_arq = f"{sku}_{idx+1}.jpg"
                caminho_local = os.path.join(FOTOS_DIR, nome_arq)
                with open(caminho_local, "wb") as f:
                    f.write(base64.b64decode(b64_data))
                externas.append({"link": f"{RAW_GITHUB_BASE}/{nome_arq}"})
    except Exception as e:
        print("Erro foto:", sku, str(e)[:100], flush=True)
    return externas


def get_bling_deposito_id(bling_headers):
    r = requests.get(f"{BLING_BASE}/depositos", headers=bling_headers)
    if r.status_code == 200:
        depositos = r.json().get("data", [])
        for d in depositos:
            if d.get("padrao"):
                return d.get("id")
        if depositos:
            return next(iter(depositos)).get("id")
    return None


def sincronizar():
    print("1. Autenticando no Bling...", flush=True)
    bling_token = get_bling_token()
    bling_headers = {
        "Authorization": f"Bearer {bling_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    deposito_id = get_bling_deposito_id(bling_headers)
    print("Deposito padrao Bling:", deposito_id, flush=True)

    print("2. Autenticando na Reval...", flush=True)
    reval_token = get_reval_token()

    print("3. Buscando catalogo e estoque da Reval...", flush=True)
    produtos_reval = get_reval_products(reval_token)
    print("Total de produtos lidos da Reval:", len(produtos_reval), flush=True)

    atualizados = 0
    criados = 0

    for item in produtos_reval:
        sku = str(item.get("codigo") or "").strip()
        if not sku:
            continue

        est_raw = str(item.get("estoque") or "").strip().upper()
        disponivel = est_raw in ("S", "SIM", "TRUE", "1", "DISPONIVEL")
        estoque_seguro = SALDO_DISPONIVEL if disponivel else 0

        nome = str(item.get("descricao") or item.get("nome") or f"Produto {sku}").strip()[:120]
        desc_curta = str(item.get("dadosAdicionais") or nome).strip()
        preco_custo = parse_preco(item.get("preco") or item.get("precoTabela") or item.get("valor"))
        preco_venda = round(preco_custo * MARGEM_VENDA, 2) if preco_custo > 0 else 10.0

        r_busca = requests.get(
            f"{BLING_BASE}/produtos",
            headers=bling_headers,
            params={"codigo": sku}
        )
        time.sleep(0.35)

        lista_bling = r_busca.json().get("data") if r_busca.status_code == 200 else []
        if lista_bling:
            prod_existente = next(iter(lista_bling))
            prod_id = prod_existente.get("id")
            if prod_id and deposito_id:
                payload_est = {
                    "produto": {"id": prod_id},
                    "deposito": {"id": deposito_id},
                    "operacao": "B",
                    "quantidade": estoque_seguro,
                    "observacoes": f"Reval status: {est_raw}"
                }
                requests.post(f"{BLING_BASE}/estoques", headers=bling_headers, json=payload_est)
                time.sleep(0.35)
                atualizados += 1

            if prod_id and disponivel:
                externas = baixar_fotos_reval(reval_token, sku)
                if externas and not prod_existente.get("imagemURL"):
                    requests.patch(
                        f"{BLING_BASE}/produtos/{prod_id}",
                        headers=bling_headers,
                        json={"midia": {"imagens": {"externas": externas}}}
                    )
                    time.sleep(0.35)
                    print(f"Foto vinculada no item existente: {sku}", flush=True)
        else:
            if not disponivel:
                continue

            externas = baixar_fotos_reval(reval_token, sku)
            payload_novo = {
                "nome": nome,
                "codigo": sku,
                "preco": preco_venda,
                "tipo": "P",
                "situacao": "A",
                "formato": "S",
                "unidade": "UN",
                "descricaoCurta": desc_curta,
                "gtin": str(item.get("codigoBarras") or "").strip(),
                "marca": str(item.get("marca") or "").strip(),
                "pesoLiquido": parse_preco(item.get("peso")),
                "pesoBruto": parse_preco(item.get("peso")),
                "dimensoes": {
                    "largura": parse_preco(item.get("largura")),
                    "altura": parse_preco(item.get("altura")),
                    "profundidade": parse_preco(item.get("comprimento")),
                    "unidadeMedida": 1
                }
            }
            if externas:
                payload_novo["midia"] = {"imagens": {"externas": externas}}

            r_cria = requests.post(f"{BLING_BASE}/produtos", headers=bling_headers, json=payload_novo)
            time.sleep(0.4)
            if r_cria.status_code in (200, 201):
                criados += 1
                prod_id = r_cria.json().get("data", {}).get("id")
                print(f"Criado ({criados}): {sku} - {nome[:30]}", flush=True)
                if prod_id and deposito_id:
                    payload_est = {
                        "produto": {"id": prod_id},
                        "deposito": {"id": deposito_id},
                        "operacao": "B",
                        "quantidade": estoque_seguro,
                        "observacoes": "Estoque inicial Reval"
                    }
                    requests.post(f"{BLING_BASE}/estoques", headers=bling_headers, json=payload_est)
                    time.sleep(0.35)
            else:
                print(f"Aviso Bling ao criar {sku}: {r_cria.status_code} {r_cria.text[:250]}", flush=True)

            if criados >= 30:
                break


if __name__ == "__main__":
    sincronizar()
