"""
Lógica de processamento de pedido: parseia linhas "SKU <sep> QTD" em formatos
flexíveis (tab, hífen, espaço) e casa cada SKU com o catálogo, tentando
correspondência exata primeiro e, se não achar, uma correspondência
aproximada baseada no "miolo" do código (tokens alfanuméricos em comum),
para lidar com pequenas diferenças de formatação entre a base do cliente
e o catálogo (hífen faltando, barra no lugar de hífen, etc).
"""
import re
from difflib import SequenceMatcher


def normalizar_forte(codigo):
    """Mantém só letras e números, maiúsculo. Usado para comparação exata."""
    if codigo is None:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", str(codigo)).upper()


def tokens_codigo(codigo):
    """Quebra o código em tokens alfanuméricos (separando por -, /, _, espaço)."""
    if codigo is None:
        return set()
    partes = re.split(r"[^A-Za-z0-9]+", str(codigo).upper())
    return {p for p in partes if p}


def parse_linha_pedido(linha):
    """
    Extrai (sku, quantidade) de uma linha de pedido em formato flexível:
    "SKU\tQTD", "SKU - QTD", "SKU: QTD", "SKU  QTD" etc.
    O SKU pode conter hífens/espaços internos (ex: "ACA-SEDAN-GG-P", "DELTA-H610 MKII").
    Retorna None se não conseguir separar um SKU de uma quantidade numérica.
    """
    linha = linha.strip()
    if not linha:
        return None

    # formato com TAB: primeiro campo é o SKU, último é a quantidade
    if "\t" in linha:
        partes = [p.strip() for p in linha.split("\t") if p.strip()]
        if len(partes) >= 2:
            try:
                qtd = float(partes[-1].replace(",", "."))
                sku = partes[0]
                if sku:
                    return sku, qtd
            except ValueError:
                pass

    # fallback: tokeniza por espaço. O ÚLTIMO token precisa ser numérico (a quantidade);
    # se o penúltimo token for apenas um separador solto ("-", ":", "–", "—"), descarta.
    tokens = linha.split()
    if len(tokens) < 2:
        return None

    try:
        qtd = float(tokens[-1].replace(",", "."))
    except ValueError:
        return None

    resto = tokens[:-1]
    if resto and resto[-1] in ("-", ":", "–", "—"):
        resto = resto[:-1]

    sku = " ".join(resto).strip()
    if not sku:
        return None

    return sku, qtd


def encontrar_correspondencia(sku_pedido, produtos, limiar_aproximado=0.72):
    """
    Procura o produto do catálogo que corresponde a `sku_pedido`.

    1. Tenta correspondência EXATA (ignorando espaços/hífens/caixa).
    2. Se não achar, tenta correspondência APROXIMADA: compara o conjunto de
       tokens alfanuméricos do código (o "miolo") e a similaridade textual,
       escolhendo o melhor candidato acima do limiar.

    Retorna (produto_ou_None, tipo) onde tipo é "exato", "aproximado" ou None.
    """
    alvo_forte = normalizar_forte(sku_pedido)
    if not alvo_forte:
        return None, None

    # 1) correspondência exata
    for p in produtos:
        if normalizar_forte(p.get("codigo")) == alvo_forte:
            return p, "exato"

    # 2) correspondência aproximada por tokens em comum + similaridade textual
    tokens_alvo = tokens_codigo(sku_pedido)
    if not tokens_alvo:
        return None, None

    melhor_produto = None
    melhor_score = 0.0

    for p in produtos:
        codigo_cat = p.get("codigo")
        tokens_cat = tokens_codigo(codigo_cat)
        if not tokens_cat:
            continue

        comuns = tokens_alvo & tokens_cat
        if not comuns:
            continue

        # overlap coefficient: quão bem os tokens em comum cobrem o menor dos dois conjuntos
        overlap = len(comuns) / min(len(tokens_alvo), len(tokens_cat))
        # similaridade textual bruta como reforço (penaliza mismatches de tamanho/ordem)
        similaridade = SequenceMatcher(None, alvo_forte, normalizar_forte(codigo_cat)).ratio()

        score = (overlap * 0.7) + (similaridade * 0.3)

        if score > melhor_score:
            melhor_score = score
            melhor_produto = p

    if melhor_produto and melhor_score >= limiar_aproximado:
        return melhor_produto, "aproximado"

    return None, None


def formatar_caixas(valor):
    """Formata a quantidade de caixas: inteiro -> '8cx', fração -> '2.25cx'."""
    arredondado = round(valor, 2)
    if arredondado == int(arredondado):
        texto = str(int(arredondado))
    else:
        texto = f"{arredondado:.2f}".rstrip("0").rstrip(".")
    return f"{texto}cx"


def processar_pedido(texto_pedido, produtos):
    """
    Processa o texto colado do pedido linha a linha.

    Retorna um dict com:
    - encontrados: lista de {sku_pedido, codigo_catalogo, nome, qtd_unidades, pcs_cx, caixas, linha_saida}
    - aproximados: mesma estrutura, mas via correspondência aproximada (precisa confirmação)
    - nao_encontrados: lista de strings com a linha original, sem alteração
    - formato_invalido: linhas que não puderam ser interpretadas como "SKU + quantidade"
    """
    encontrados = []
    aproximados = []
    nao_encontrados = []
    formato_invalido = []

    for linha_original in texto_pedido.split("\n"):
        linha_original = linha_original.strip()
        if not linha_original:
            continue

        parsed = parse_linha_pedido(linha_original)
        if not parsed:
            formato_invalido.append(linha_original)
            continue

        sku_pedido, qtd = parsed
        produto, tipo = encontrar_correspondencia(sku_pedido, produtos)

        if not produto:
            nao_encontrados.append(linha_original)
            continue

        pcs_cx = produto.get("pcs_cx")
        if not pcs_cx:
            # achou o produto mas não sabemos o PCS/CX -> não dá pra converter com segurança
            nao_encontrados.append(f"{linha_original}  (encontrado no catálogo, mas sem PCS/CX identificado)")
            continue

        caixas = qtd / pcs_cx
        item = {
            "sku_pedido": sku_pedido,
            "codigo_catalogo": produto.get("codigo"),
            "nome": produto.get("nome"),
            "qtd_unidades": qtd,
            "pcs_cx": pcs_cx,
            "preco": produto.get("preco"),
            "caixas": round(caixas, 2),
            "linha_saida": f"{sku_pedido}\t{formatar_caixas(caixas)}",
        }

        if tipo == "exato":
            encontrados.append(item)
        else:
            aproximados.append(item)

    return {
        "encontrados": encontrados,
        "aproximados": aproximados,
        "nao_encontrados": nao_encontrados,
        "formato_invalido": formato_invalido,
    }
