import io
import math
import os
import re
import tempfile

import pandas as pd
import requests
from flask import Flask, jsonify, render_template, request

from parser import extrair_catalogo
from pedido import processar_pedido

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB


def normalizar_codigo(codigo):
    if codigo is None:
        return ""
    # remove espaços, hífens e colchetes (alguns SKUs vêm como "[EKLY-0137]")
    return re.sub(r"[\s\-\[\]]", "", str(codigo)).upper()


def extrair_id_planilha_google(url):
    """Extrai o ID de um link do Google Sheets (docs.google.com/spreadsheets/d/<ID>/...)."""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else None


def carregar_dataframe_de_bytes(nome_arquivo, conteudo):
    if nome_arquivo.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(conteudo))
    return pd.read_excel(io.BytesIO(conteudo))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/extrair", methods=["POST"])
def api_extrair():
    arquivo = request.files.get("pdf")
    if not arquivo:
        return jsonify({"erro": "Nenhum PDF enviado"}), 400

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        arquivo.save(tmp.name)
        caminho_tmp = tmp.name

    try:
        produtos = extrair_catalogo(caminho_tmp)
    finally:
        os.unlink(caminho_tmp)

    return jsonify({"total": len(produtos), "produtos": produtos})


@app.route("/api/comparar", methods=["POST"])
def api_comparar():
    """
    Espera:
    - 'planilha': arquivo .xlsx/.csv com colunas (nomes flexíveis, ver abaixo)
    - 'produtos': JSON (lista) com o resultado de /api/extrair, reenviado pelo front-end
    - 'quantidade_col': nome da coluna de quantidade em unidades na planilha (opcional)

    Colunas esperadas na planilha (case-insensitive, com variações comuns):
    - codigo / código / sku / referencia
    - preco / preço / valor
    - quantidade / qtd / unidades (opcional, para conversão em caixas)
    """
    planilha = request.files.get("planilha")
    if not planilha:
        return jsonify({"erro": "Nenhuma planilha enviada"}), 400

    import json

    produtos_json = request.form.get("produtos")
    if not produtos_json:
        return jsonify({"erro": "Lista de produtos do catálogo não enviada"}), 400
    produtos = json.loads(produtos_json)

    nome_arquivo = planilha.filename.lower()
    conteudo = planilha.read()
    try:
        if nome_arquivo.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(conteudo))
        else:
            df = pd.read_excel(io.BytesIO(conteudo))
    except Exception as e:
        return jsonify({"erro": f"Não foi possível ler a planilha: {e}"}), 400

    # normaliza nomes de coluna
    df.columns = [str(c).strip().lower() for c in df.columns]

    col_codigo = next((c for c in df.columns if c in ("codigo", "código", "sku", "referencia", "referência")), None)
    col_preco = next((c for c in df.columns if c in ("preco", "preço", "valor")), None)
    col_qtd = next((c for c in df.columns if c in ("quantidade", "qtd", "unidades", "qtde")), None)

    if not col_codigo:
        return jsonify({"erro": "Não encontrei uma coluna de código/SKU na planilha (aceito: codigo, código, sku, referencia)"}), 400

    # monta índice do catálogo por código normalizado
    catalogo_por_codigo = {}
    for p in produtos:
        catalogo_por_codigo[normalizar_codigo(p.get("codigo"))] = p

    resultado = []
    for _, linha in df.iterrows():
        codigo_planilha = linha.get(col_codigo)
        codigo_norm = normalizar_codigo(codigo_planilha)
        item_catalogo = catalogo_por_codigo.get(codigo_norm)

        preco_planilha = None
        if col_preco:
            try:
                preco_planilha = float(linha.get(col_preco))
            except (TypeError, ValueError):
                preco_planilha = None

        qtd_unidades = None
        if col_qtd:
            try:
                qtd_unidades = float(linha.get(col_qtd))
            except (TypeError, ValueError):
                qtd_unidades = None

        if item_catalogo:
            preco_catalogo = item_catalogo.get("preco")
            pcs_cx = item_catalogo.get("pcs_cx")

            diff = None
            bate = None
            if preco_planilha is not None and preco_catalogo is not None:
                diff = round(preco_planilha - preco_catalogo, 2)
                bate = abs(diff) < 0.01

            caixas = None
            unidades_restantes = None
            if qtd_unidades is not None and pcs_cx:
                caixas = math.floor(qtd_unidades / pcs_cx)
                unidades_restantes = qtd_unidades - (caixas * pcs_cx)

            resultado.append({
                "codigo_planilha": codigo_planilha,
                "codigo_catalogo": item_catalogo.get("codigo"),
                "nome": item_catalogo.get("nome"),
                "preco_planilha": preco_planilha,
                "preco_catalogo": preco_catalogo,
                "diferenca_preco": diff,
                "preco_bate": bate,
                "pcs_cx": pcs_cx,
                "quantidade_unidades": qtd_unidades,
                "caixas_fechadas": caixas,
                "unidades_restantes": unidades_restantes,
                "encontrado": True,
            })
        else:
            resultado.append({
                "codigo_planilha": codigo_planilha,
                "codigo_catalogo": None,
                "nome": None,
                "preco_planilha": preco_planilha,
                "preco_catalogo": None,
                "diferenca_preco": None,
                "preco_bate": None,
                "pcs_cx": None,
                "quantidade_unidades": qtd_unidades,
                "caixas_fechadas": None,
                "unidades_restantes": None,
                "encontrado": False,
            })

    return jsonify({"resultado": resultado})


@app.route("/api/comparar_custo", methods=["POST"])
def api_comparar_custo():
    """
    Compara o preço do catálogo com uma base de custos, aplicando a regra:
        custo_esperado = preco_catalogo * (1 - desconto)
    (desconto padrão de 10%, ou seja, preco_catalogo * 0.9)

    Espera (multipart/form-data):
    - 'produtos': JSON com o resultado de /api/extrair
    - 'desconto' (opcional, padrão 0.10): fração de desconto, ex. 0.10 = 10%
    - 'tolerancia' (opcional, padrão 0.02): tolerância relativa para considerar "bate"
    - E UM dos dois:
        - 'planilha': arquivo .xlsx/.csv com colunas 'sku'/'codigo' e 'custo'
        - 'sheet_url': link do Google Sheets (planilha precisa estar com
          compartilhamento "qualquer pessoa com o link pode ver")
    """
    import json

    produtos_json = request.form.get("produtos")
    if not produtos_json:
        return jsonify({"erro": "Lista de produtos do catálogo não enviada"}), 400
    produtos = json.loads(produtos_json)

    desconto = float(request.form.get("desconto", 0.10))
    tolerancia = float(request.form.get("tolerancia", 0.02))

    planilha = request.files.get("planilha")
    sheet_url = request.form.get("sheet_url")

    if planilha:
        conteudo = planilha.read()
        try:
            df = carregar_dataframe_de_bytes(planilha.filename, conteudo)
        except Exception as e:
            return jsonify({"erro": f"Não foi possível ler a planilha: {e}"}), 400
    elif sheet_url:
        file_id = extrair_id_planilha_google(sheet_url)
        if not file_id:
            return jsonify({"erro": "Não reconheci esse link como uma URL do Google Sheets"}), 400
        export_url = f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx"
        try:
            resp = requests.get(export_url, timeout=30)
            resp.raise_for_status()
            df = carregar_dataframe_de_bytes("planilha.xlsx", resp.content)
        except Exception as e:
            return jsonify({"erro": f"Não consegui baixar a planilha do Google Sheets (verifique se o compartilhamento está como \"qualquer pessoa com o link\"): {e}"}), 400
    else:
        return jsonify({"erro": "Envie uma planilha ou um link do Google Sheets"}), 400

    df.columns = [str(c).strip().lower() for c in df.columns]
    col_codigo = next((c for c in df.columns if c in ("codigo", "código", "sku", "referencia", "referência")), None)
    col_custo = next((c for c in df.columns if c in ("custo", "preco", "preço", "valor", "cost")), None)

    if not col_codigo or not col_custo:
        return jsonify({"erro": "Não encontrei as colunas de SKU/código e custo na planilha (aceito: sku/codigo + custo/preco/valor)"}), 400

    custos_por_codigo = {}
    for _, linha in df.iterrows():
        sku = linha.get(col_codigo)
        if sku is None:
            continue
        try:
            custo = float(linha.get(col_custo))
        except (TypeError, ValueError):
            continue
        custos_por_codigo[normalizar_codigo(sku)] = {"sku_original": sku, "custo": custo}

    resultado = []
    for p in produtos:
        preco_catalogo = p.get("preco")
        if preco_catalogo is None:
            continue  # sem preço no catálogo, não há o que comparar

        item_custo = custos_por_codigo.get(normalizar_codigo(p.get("codigo")))
        if not item_custo:
            resultado.append({
                "codigo": p.get("codigo"),
                "nome": p.get("nome"),
                "preco_catalogo": preco_catalogo,
                "custo_planilha": None,
                "custo_esperado": round(preco_catalogo * (1 - desconto), 2),
                "diferenca": None,
                "diferenca_percentual": None,
                "bate": None,
                "encontrado_na_planilha": False,
            })
            continue

        custo_esperado = round(preco_catalogo * (1 - desconto), 2)
        custo_real = item_custo["custo"]
        diff = round(custo_real - custo_esperado, 2)
        diff_pct = round((diff / custo_esperado) * 100, 2) if custo_esperado else None
        bate = abs(diff_pct) <= (tolerancia * 100) if diff_pct is not None else False

        resultado.append({
            "codigo": p.get("codigo"),
            "nome": p.get("nome"),
            "preco_catalogo": preco_catalogo,
            "custo_planilha": custo_real,
            "custo_esperado": custo_esperado,
            "diferenca": diff,
            "diferenca_percentual": diff_pct,
            "bate": bate,
            "encontrado_na_planilha": True,
        })

    return jsonify({"resultado": resultado, "desconto_usado": desconto, "tolerancia_usada": tolerancia})


@app.route("/api/pedido", methods=["POST"])
def api_pedido():
    """
    Converte um pedido colado (formato "SKU <sep> QTD", uma linha por item)
    para caixas, usando o PCS/CX do catálogo já extraído.

    Espera (JSON ou form):
    - 'produtos': lista de produtos (resultado de /api/extrair)
    - 'pedido_texto': texto colado pelo usuário

    Faz correspondência exata de SKU e, se não achar, tenta uma correspondência
    aproximada (baseada nos tokens em comum do código) para lidar com pequenas
    diferenças de formatação entre a base do cliente e o catálogo.
    """
    dados = request.get_json(silent=True) or request.form
    produtos_raw = dados.get("produtos")
    pedido_texto = dados.get("pedido_texto")

    if produtos_raw is None or not pedido_texto:
        return jsonify({"erro": "Envie 'produtos' (catálogo extraído) e 'pedido_texto'"}), 400

    import json

    produtos = produtos_raw if isinstance(produtos_raw, list) else json.loads(produtos_raw)

    resultado = processar_pedido(pedido_texto, produtos)
    return jsonify(resultado)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
