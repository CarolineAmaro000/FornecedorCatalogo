# Conversor de Catálogo PDF → Comparação com Planilha

Ferramenta web para:
1. Subir um catálogo em PDF (padrão com cards de produto: código, nome, especificações, PCS/CX e preço).
2. Extrair cada produto como um bloco estruturado (usando a posição da foto de cada card no PDF).
3. Colar um pedido no formato `SKU<TAB>QUANTIDADE` ou `SKU - QUANTIDADE` (unidades) e converter automaticamente para caixas, usando o PCS/CX de cada item do catálogo. Itens esgotados, não encontrados, ou com SKU levemente diferente do catálogo são tratados separadamente (ver abaixo).
4. Subir uma planilha (.xlsx ou .csv) e comparar preços produto a produto.
5. Converter a quantidade de unidades em caixas fechadas + unidades restantes, usando o PCS/CX de cada item (via planilha).
6. Verificar se o preço do catálogo bate com uma **base de custo** (planilha própria ou link público do Google Sheets), aplicando automaticamente o desconto de 10% (configurável) que a empresa recebe sobre o custo de tabela.

## Verificação de preço x base de custo (10% de desconto)

Regra aplicada: `custo_esperado = preço_do_catálogo × (1 − desconto)` — com desconto padrão de 10%
(ou seja, `preço_catálogo × 0.9`). O valor calculado é comparado com o custo real da planilha,
dentro de uma tolerância configurável (padrão 2%).

Você pode:
- Colar o link de uma planilha do Google Sheets (ela precisa estar com o compartilhamento
  "Qualquer pessoa com o link pode visualizar" — o backend busca o conteúdo direto pela URL, sem
  precisar baixar/subir manualmente), **ou**
- Subir um arquivo `.xlsx`/`.csv` com colunas de código/SKU e custo.

Colunas aceitas na base de custo: `sku`/`codigo`/`referencia` (código) e `custo`/`preco`/`valor` (valor).

Produtos do catálogo que não tiverem preço identificado na extração são ignorados nessa checagem
(não há o que comparar). Produtos cujo código não aparece na base de custo são listados como
"não encontrado", sem contar como divergência.


## Conversão de pedido (unidades → caixas)

Cole o pedido, uma linha por item, em qualquer um destes formatos:

```
AIN-1208	16
ACA-SEDAN-GG-P - 105
```

A saída é gerada no mesmo formato do SKU enviado, com a quantidade já convertida:

```
AIN-1208	8cx
```

O resultado é dividido em 4 grupos:
- **Encontrados** — SKU bateu exatamente com o catálogo (ignorando espaços/hífens/caixa). Pronto para copiar/usar.
- **Aproximados** — o SKU do pedido não bateu exatamente, mas o sistema achou um código parecido no catálogo
  (comparando o "miolo" alfanumérico do código — útil quando falta um hífen, sobra uma barra, ou o código do
  cliente tem um prefixo/sufixo diferente do catálogo, ex: `KP/H61K/8GB` casando com `KP-H61K-8GB`). Esses itens
  aparecem destacados, mostrando o SKU original e o código do catálogo usado, **para você confirmar antes de
  considerar válido**.
- **Não encontrados** — nenhuma correspondência (exata ou aproximada) foi encontrada. Devolvidos exatamente como
  foram enviados, sem alteração — inclui automaticamente os itens **ESGOTADOS** (o parser já os descarta na
  extração do catálogo, então nunca aparecem convertidos).
- **Formato inválido** — linhas que não puderam ser interpretadas como "SKU + quantidade".

Regras:
- O SKU é comparado ignorando espaços, hífens e maiúsculas/minúsculas na correspondência exata.
- Se a quantidade não for múltipla exata do PCS/CX, o resultado sai com até 2 casas decimais (ex: `2.25cx`) —
  revise esses casos manualmente antes de fechar o pedido.
- Se o próprio código do produto tiver espaço no meio (ex: `DELTA-H610 MKII`), o sistema entende o texto até o
  separador/número final como parte do SKU.

## Como funciona a extração

O parser (`parser.py`) usa a posição de cada **imagem de produto** no PDF para
delimitar onde começa e termina cada card. Isso evita o problema comum de
extração de PDF que embaralha texto de cards vizinhos.

Funciona bem em catálogos no formato grid (3 colunas de cards por página, cada
card com uma foto). Se o seu catálogo tiver um layout muito diferente, os
parâmetros `margem_topo` e `margem_lateral` em `parser.py` podem precisar de
ajuste.

**Limitação conhecida:** ~5-10% dos cards podem sair com preço ou PCS/CX não
identificado, geralmente em páginas com grade irregular (número de colunas
diferente, linhas de tamanhos desiguais). Esses itens aparecem destacados em
amarelo na interface — o campo `raw` traz o texto bruto extraído para
conferência manual.

## Rodando localmente

```bash
pip install -r requirements.txt
python app.py
```

Acesse http://localhost:5000

## Deploy (GitHub + Render, grátis)

GitHub Pages **não** roda Python — por isso este projeto precisa de um host
que execute o backend Flask. Render tem um plano free simples de configurar:

1. Suba esta pasta para um repositório no GitHub.
2. Crie uma conta em https://render.com e conecte seu GitHub.
3. "New +" → "Web Service" → selecione o repositório.
4. Configuração:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn app:app`
5. Deploy. Em alguns minutos você terá uma URL pública (ex:
   `https://seu-app.onrender.com`) já com a interface funcionando.

Alternativas equivalentes: Railway.app, Fly.io, PythonAnywhere.

## Planilha de comparação — formato esperado

Colunas aceitas (não sensível a maiúsculas/acentos):

| Coluna         | Aceita também            | Obrigatória? |
|-----------------|---------------------------|--------------|
| código do produto | `codigo`, `sku`, `referencia` | Sim |
| preço           | `preco`, `valor`          | Não (só se quiser comparar preço) |
| quantidade      | `qtd`, `unidades`, `qtde` | Não (só se quiser converter em caixas) |

O código é comparado ignorando espaços, hífens e maiúsculas/minúsculas
(`DELTA-H510M2K` = `delta h510 m2k`).

## Estrutura do projeto

```
app.py              -> backend Flask (rotas /api/extrair e /api/comparar)
parser.py            -> lógica de extração dos cards do PDF
templates/index.html -> front-end (upload, tabelas, download do resultado)
requirements.txt
Procfile              -> comando de start para Render/Railway
```
