"""
Extrai cada card de produto de um catálogo PDF (padrão Delta Gaming / similar),
usando a posição da foto de cada produto para delimitar o box e agrupar
código + nome + especificações + PCS/CX + preço corretamente.
"""
import re
import pdfplumber


def agrupar_colunas(images, tolerancia=40):
    """Agrupa imagens em colunas com base no x0 (posição horizontal)."""
    cols = []
    for im in sorted(images, key=lambda i: i["x0"]):
        colocado = False
        for c in cols:
            if abs(c[0]["x0"] - im["x0"]) < tolerancia:
                c.append(im)
                colocado = True
                break
        if not colocado:
            cols.append([im])
    return cols


def extrair_boxes_pagina(page, margem_topo=30, margem_lateral=45):
    """Retorna uma lista de textos brutos, um por card de produto da página."""
    boxes_texto = []
    images = page.images
    if not images:
        return boxes_texto

    colunas = agrupar_colunas(images)
    colunas = sorted(colunas, key=lambda c: c[0]["x0"])

    for col_imgs in colunas:
        col_imgs = sorted(col_imgs, key=lambda i: i["top"])
        x_left = min(i["x0"] for i in col_imgs) - margem_lateral
        x_right = max(i["x1"] for i in col_imgs) + margem_lateral

        for i, img in enumerate(col_imgs):
            top_box = img["top"] - margem_topo
            if i + 1 < len(col_imgs):
                bottom_box = col_imgs[i + 1]["top"] - margem_topo
            else:
                bottom_box = page.height

            x0f = max(0, x_left)
            x1f = min(page.width, x_right)
            topf = max(0, top_box)
            bottomf = min(page.height, bottom_box)

            if x1f <= x0f or bottomf <= topf:
                continue

            crop = page.crop((x0f, topf, x1f, bottomf))
            texto = (crop.extract_text() or "").strip()
            if texto:
                boxes_texto.append(texto)

    return boxes_texto


RE_PRECO = re.compile(r"R\$\s*([\d\.,]+)")
RE_PCS_CX = re.compile(r"PCS\s*/\s*CX:\s*(\d+)")


def estruturar_box(texto_bruto):
    """Converte o texto bruto de um card em um dicionário estruturado."""
    linhas = [l.strip() for l in texto_bruto.split("\n") if l.strip()]
    if not linhas:
        return None

    # remove rodapé repetido
    linhas = [l for l in linhas if l.upper() != "VOLTAR AO INÍCIO"]
    if not linhas:
        return None

    # descarta boxes "ESGOTADO" (sem produto real)
    if all(re.fullmatch(r"ESGOTADO", l, re.IGNORECASE) for l in linhas):
        return None

    codigo = linhas[0]
    resto = linhas[1:]

    nome_linhas = []
    specs = []
    for l in resto:
        if l.startswith("•"):
            specs.append(l.lstrip("•").strip())
        elif l.upper().startswith("UNID.") or "PCS/CX" in l.upper() or l.upper().startswith("R$"):
            continue
        elif not specs:
            # ainda estamos no bloco do nome (antes do primeiro bullet)
            nome_linhas.append(l)

    nome = " ".join(nome_linhas).strip()

    preco = None
    m = RE_PRECO.search(texto_bruto)
    if m:
        try:
            preco = float(m.group(1).replace(",", ""))
        except ValueError:
            preco = None

    pcs_cx = None
    m2 = RE_PCS_CX.search(texto_bruto)
    if m2:
        pcs_cx = int(m2.group(1))

    return {
        "codigo": codigo,
        "nome": nome,
        "especificacoes": specs,
        "pcs_cx": pcs_cx,
        "preco": preco,
        "raw": texto_bruto,
    }


def extrair_catalogo(caminho_pdf, pular_paginas_iniciais=4):
    """
    Percorre todo o PDF e retorna uma lista de produtos estruturados.
    `pular_paginas_iniciais` ignora capa/menu de categorias (sem produtos reais).
    """
    produtos = []
    with pdfplumber.open(caminho_pdf) as pdf:
        for num_pagina, page in enumerate(pdf.pages, start=1):
            if num_pagina <= pular_paginas_iniciais:
                continue
            if not page.images:
                continue

            boxes = extrair_boxes_pagina(page)
            for texto_bruto in boxes:
                item = estruturar_box(texto_bruto)
                if item:
                    item["pagina"] = num_pagina
                    produtos.append(item)

    return produtos
