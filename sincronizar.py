import os
import json
import base64
import time
import requests

REVAL_BASE = "https://" + "api.reval.net/api"
BLING_BASE = "https://" + "://bling.com.br/Api/v3"

REVAL_USER = os.environ["REVAL_USER"]
REVAL_PASS = os.environ["REVAL_PASS"]
BLING_CLIENT_ID = os.environ["BLING_CLIENT_ID"]
BLING_CLIENT_SECRET = os.environ["BLING_CLIENT_SECRET"]
BLING_AUTH_CODE = os.environ.get("BLING_AUTH_CODE", "")

TOKEN_FILE = "bling_tokens.json"
SALDO_SEGURANCA = 5


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
            "refresh_token": tokens["refresh_token"]
        }
        r = requests.post(f"{BLING_BASE}/oauth/token", headers=headers, data=data)
        if r.status_code == 200:
            new_tokens = r.json()
            with open(TOKEN_FILE, "w") as f:
                json.dump(new_tokens, f)
            return new_tokens["access_token"]
        else:
            print("Erro ao renovar token:", r.text)

    if not BLING_AUTH_CODE:
        raise Exception("BLING_AUTH_CODE ausente e nenhum refresh_token salvo!")

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
    return new_tokens["access_token"]


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
    headers = {"Authorization": f"Bearer {reval_token}"}
    produtos = []
    pagina = 1
    tamanho = 100
    while True:
        r = requests.get(
            f"{REVAL_BASE}/produto/get-page-tabela",
            headers=headers,
            params={
                "usuario": REVAL_USER,
                "pageIndex": pagina,
                "pageSize": tamanho
            }
        )
        if r.status_code != 200:
            print(f"Aviso na pagina {pagina} da Reval:", r.status_code, r.text[:250])
            break
        resp = r.json()
        if isinstance(resp, list):
            lista = resp
        elif isinstance(resp, dict):
            lista = resp.get("data") or resp.get("produtos") or resp.get("itens") or resp.get("items") or []
        else:
            lista = []

        if not lista:
            break
        produtos.extend(lista)
        if len(lista) < tamanho:
            break
        pagina += 1
        if pagina > 15:
            break
    return produtos


def get_bling_deposito_id(bling_headers):
    r = requests.get(f"{BLING_BASE}/depositos", headers=bling_headers)
    if r.status_code == 200:
        depositos = r.json().get("data", [])
        for d in depositos:
            if d.get("padrao"):
                return d["id"]
        if depositos:
            return depositos[0]["id"]
    return None


def sincronizar():
    print("1. Autenticando no Bling...")
    bling_token = get_bling_token()
    bling_headers = {
        "Authorization": f"Bearer {bling_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    deposito_id = get_bling_deposito_id(bling_headers)
    print("Deposito padrao Bling:", deposito_id)

    print("2. Autenticando na Reval...")
    reval_token = get_reval_token()

    print("3. Buscando catalogo e estoque da Reval...")
    produtos_reval = get_reval_products(reval_token)
    print(f"Total de produtos encontrados na Reval: {len(produtos_reval)}")

    if produtos_reval:
        print("Exemplo de campos do primeiro item:", list(produtos_reval[0].keys()))

    atualizados = 0
    criados = 0

    for item in produtos_reval[:1000]:
        sku = str(
            item.get("codigo")
            or item.get("codigoProduto")
            or item.get("codProduto")
            or item.get("sku")
            or item.get("id")
            or ""
        ).strip()
        if not sku:
            continue

        nome = str(
            item.get("descricao")
            or item.get("descricaoProduto")
            or item.get("nome")
            or f"Produto {sku}"
        )[:120]

        preco_custo = float(
            item.get("preco")
            or item.get("precoTabela")
            or item.get("valor")
            or item.get("precoUnitario")
            or 0
        )

        estoque_real = int(float(
            item.get("estoque")
            or item.get("saldo")
            or item.get("quantidade")
            or item.get("qtdEstoque")
            or 0
        ))

        estoque_seguro = estoque_real if estoque_real >= SALDO_SEGURANCA else 0
        preco_venda = round(preco_custo * 1.65, 2) if preco_custo > 0 else 0

        r_busca = requests.get(
            f"{BLING_BASE}/produtos",
            headers=bling_headers,
            params={"codigo": sku}
        )
        time.sleep(0.35)

        if r_busca.status_code == 200 and r_busca.json().get("data"):
            prod_id = r_busca.json()["data"][0]["id"]
            if deposito_id:
                payload_est = {
                    "produto": {"id": prod_id},
                    "deposito": {"id": deposito_id},
                    "operacao": "B",
                    "quantidade": estoque_seguro,
                    "observacoes": f"Reval real: {estoque_real} | Seguro: {estoque_seguro}"
                }
                requests.post(f"{BLING_BASE}/estoques", headers=bling_headers, json=payload_est)
                time.sleep(0.35)
                atualizados += 1
        else:
            if estoque_seguro == 0:
                continue
            payload_novo = {
                "nome": nome,
                "codigo": sku,
                "preco": preco_venda,
                "tipo": "P",
                "situacao": "A",
                "formato": "S",
                "unidade": "UN"
            }
            r_cria = requests.post(f"{BLING_BASE}/produtos", headers=bling_headers, json=payload_novo)
            time.sleep(0.35)
            if r_cria.status_code in (200, 201):
                criados += 1
                prod_id = r_cria.json().get("data", {}).get("id")
                if prod_id and deposito_id:
                    payload_est = {
                        "produto": {"id": prod_id},
                        "deposito": {"id": deposito_id},
                        "operacao": "B",
                        "quantidade": estoque_seguro,
                        "observacoes": f"Estoque inicial Reval ({estoque_real})"
                    }
                    requests.post(f"{BLING_BASE}/estoques", headers=bling_headers, json=payload_est)
                    time.sleep(0.35)

    print(f"Concluido! Produtos novos: {criados} | Estoques atualizados: {atualizados}")


if __name__ == "__main__":
    sincronizar()
