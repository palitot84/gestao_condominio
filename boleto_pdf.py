"""Leitura completa de boletos condominiais em PDF com camada de texto (sem OCR).

Extrai cobrança, encargos, pagador, correspondência, demonstrativo de receitas e despesas
(todos os níveis), resumo financeiro por conta e inadimplência. Somas, saldos, duplicidades
entre recibo e ficha e dígitos verificadores são conferidos; qualquer linha não reconhecida
interrompe a leitura. O boleto importado fica PENDENTE: pagamento não é inferido.
"""

import hashlib
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO

import pypdf
from pypdf import PdfReader

MESES = {"JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
         "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12}
VALOR = r"-?[\d.]*\d,\d{2}"
CNPJ = r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}"
# Largura da coluna de nome no resumo financeiro: partes cheias foram quebradas no meio da palavra.
LARGURA_NOME_CONTA = 15
TITULOS_SECOES = {
    "DEMONSTRATIVO DE RECEITAS E DESPESAS": "demonstrativo",
    "RESUMO FINANCEIRO": "resumo",
    "SALDO DA INADIMPLÊNCIA": "inadimplencia",
}


def _exigir(condicao, mensagem: str) -> None:
    if not condicao:
        raise ValueError(mensagem)


def _dinheiro(valor: str) -> Decimal:
    return Decimal(valor.replace(".", "").replace(",", "."))


def _data(valor: str) -> date:
    return datetime.strptime(valor, "%d/%m/%Y").date()


def _normalizar(texto: str) -> str:
    return " ".join(texto.split())


def _chave(texto: str) -> str:
    return _normalizar(texto).casefold()


def _competencia_texto(texto: str) -> date:
    mes, ano = texto.strip().split("/")
    _exigir(mes.upper()[:3] in MESES, f"Mês inválido: {texto}")
    return date(int(ano) + (2000 if len(ano) == 2 else 0), MESES[mes.upper()[:3]], 1)


def _json(valor):
    if isinstance(valor, dict):
        return {chave: _json(item) for chave, item in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_json(item) for item in valor]
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    return valor


def _modulo10(digitos: str) -> int:
    total = 0
    for posicao, digito in enumerate(reversed(digitos)):
        produto = int(digito) * (2 if posicao % 2 == 0 else 1)
        total += produto // 10 + produto % 10
    return (10 - total % 10) % 10


def _modulo11(digitos: str) -> int:
    total = sum(int(digito) * (2 + posicao % 8) for posicao, digito in enumerate(reversed(digitos)))
    resultado = 11 - total % 11
    return 1 if resultado in (0, 10, 11) else resultado


def decodificar_linha_digitavel(linha: str) -> dict:
    """Converte a linha digitável (47 dígitos) no código de barras, conferindo os dígitos verificadores."""
    _exigir(re.fullmatch(r"\d{47}", linha), "A linha digitável deve ter 47 dígitos.")
    for inicio, fim in ((0, 9), (10, 20), (21, 31)):
        _exigir(_modulo10(linha[inicio:fim]) == int(linha[fim]),
                "Dígito verificador de campo da linha digitável inválido.")
    codigo = linha[0:4] + linha[32] + linha[33:47] + linha[4:9] + linha[10:20] + linha[21:31]
    _exigir(_modulo11(codigo[:4] + codigo[5:]) == int(codigo[4]),
            "Dígito verificador geral do código de barras inválido.")
    fator = int(codigo[5:9])
    return {
        "codigo_barras": codigo, "banco": codigo[:3], "fator_vencimento": fator,
        "valor": Decimal(int(codigo[9:19])) / 100,
        # FEBRABAN reiniciou o fator em 1000 no dia 22/02/2025.
        "vencimentos_possiveis": {date(1997, 10, 7) + timedelta(days=fator),
                                  date(2025, 2, 22) + timedelta(days=fator - 1000)},
    }


def _ler_cabecalho(texto: str) -> dict:
    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
    linhas = [linha for linha in linhas if not re.fullmatch(r"_{5,}", linha)]

    def indice(padrao: str, descricao: str) -> int:
        achados = [i for i, linha in enumerate(linhas) if re.fullmatch(padrao, linha)]
        _exigir(achados, f"{descricao} não identificado no PDF.")
        return achados[0]

    def repetido(padrao: str, descricao: str) -> tuple:
        achados = [re.fullmatch(padrao, linha) for linha in linhas]
        achados = [m.groups() for m in achados if m]
        _exigir(achados, f"{descricao} não identificado no PDF.")
        _exigir(len({tuple(_normalizar(g) for g in grupo) for grupo in achados}) == 1,
                f"{descricao} diverge entre o recibo do pagador e a ficha de compensação.")
        return tuple(_normalizar(g) for g in achados[0])

    via = re.fullmatch(r"(\d+ª via)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})", linhas[0])
    _exigir(via, "Identificação da via não encontrada no PDF.")
    emitente_cabecalho = linhas[1]
    i_recibo = indice(r"Recibo do Pagador", "Recibo do pagador")
    beneficiario_recibo = _normalizar(" ".join(linhas[2:i_recibo]))
    competencia = linhas[indice(r"\d{2}/\d{4}", "Competência")]
    i_unidade = indice(r"Unidade", "Unidade")
    unidade = linhas[i_unidade + 1]
    agencia, = repetido(r"(\d{4}/\d{7}-\d)", "Agência/código do beneficiário")
    nosso_numero, = repetido(r"(\d{3}/\d{11}-\d)", "Nosso número")
    vencimentos = [linhas[i] for i in range(len(linhas) - 1)
                   if re.fullmatch(r"\d{2}/\d{2}/\d{4}", linhas[i]) and re.fullmatch(VALOR, linhas[i + 1])]
    valores = [linhas[i + 1] for i in range(len(linhas) - 1)
               if re.fullmatch(r"\d{2}/\d{2}/\d{4}", linhas[i]) and re.fullmatch(VALOR, linhas[i + 1])]
    _exigir(vencimentos and len(set(vencimentos)) == 1 and len(set(valores)) == 1,
            "Vencimento e valor divergem entre o recibo do pagador e a ficha de compensação.")
    documento = linhas[indice(r"N\. Doc", "Número do documento") + 1]
    _exigir(re.fullmatch(r"\d+", documento), "Número do documento inválido.")
    banco = re.fullmatch(r"(\d{3}-\d)\s+(\d{5}\.\d{5}\s+\d{5}\.\d{6}\s+\d{5}\.\d{6}\s+\d\s+\d{14})",
                         linhas[indice(r"\d{3}-\d\s+[\d.\s]+", "Linha digitável")])
    _exigir(banco, "Linha digitável em formato não reconhecido.")
    i_local = indice(r"Pagável.+", "Local de pagamento")
    i_datas = indice(rf"\d{{2}}/\d{{2}}/\d{{4}}\s+{documento}\s+\S+\s+\d{{2}}/\d{{2}}/\d{{4}}",
                     "Datas do documento e de processamento")
    datas = re.fullmatch(r"(\S+)\s+\S+\s+(\S+)\s+(\S+)", linhas[i_datas])
    i_carteira = indice(r"\d{3}\s+R\$", "Carteira e espécie")
    carteira, especie = linhas[i_carteira].split()

    beneficiario_ficha = _normalizar(" ".join(linhas[i_local + 1:i_datas]))
    beneficiario = re.fullmatch(rf"(.+?)\s+\(({CNPJ})\)\s+(.+?)\s*/\s*CNPJ:\s*({CNPJ})", beneficiario_ficha)
    _exigir(beneficiario and beneficiario[2] == beneficiario[4], "Beneficiário ou CNPJ não identificado no PDF.")
    _exigir(beneficiario_recibo.startswith(f"{beneficiario[1]} ({beneficiario[2]})"),
            "Beneficiário diverge entre o recibo do pagador e a ficha de compensação.")
    endereco = re.fullmatch(r"(.+?),\s*(?:N[ºo°]\s*)?(\d+)\s+(.+?)\s*-\s*(.+?)\s+([A-Z]{2})\s+(\d{5}-?\d{3})",
                            beneficiario[3])
    _exigir(endereco, "Endereço do beneficiário não identificado no PDF.")

    pagadores = [i for i, linha in enumerate(linhas) if re.fullmatch(r".+?\s+\(\*{3}\d+\*{3}\)", linha)]
    _exigir(len(pagadores) == 2, "Pagador não identificado no recibo e na ficha de compensação.")
    pagador_nome, pagador_cpf = repetido(r"(.+?)\s+\((\*{3}\d+\*{3})\)", "Pagador")
    instrucoes = linhas[i_carteira + 1:pagadores[1]]
    rodape_ficha = linhas[pagadores[1] + 1:]
    _exigir(len(rodape_ficha) == 3, "Endereço do pagador ou sacador/avalista não identificado no PDF.")
    pagador_endereco = re.fullmatch(r"(.+?)\s*\((\S+)\s*\)\s*(.*)", rodape_ficha[0])
    pagador_cidade = re.fullmatch(r"(\d{5}-?\d{3})\s+(.+)-([A-Z]{2})", rodape_ficha[1])
    sacador = re.fullmatch(rf"(.+?)\s+({CNPJ})", rodape_ficha[2])
    _exigir(pagador_endereco and pagador_cidade and sacador, "Endereço do pagador não identificado no PDF.")
    _exigir(pagador_endereco[2] == unidade, "A unidade do endereço do pagador diverge da unidade do boleto.")
    _exigir(sacador[2] == beneficiario[2], "O CNPJ do sacador/avalista diverge do beneficiário.")

    linha_digitavel = re.sub(r"\D", "", banco[2])
    decodificado = decodificar_linha_digitavel(linha_digitavel)
    vencimento, valor = _data(vencimentos[0]), _dinheiro(valores[0])
    _exigir(decodificado["banco"] == banco[1][:3], "O banco da linha digitável diverge do código impresso.")
    _exigir(decodificado["valor"] == valor, "O valor da linha digitável diverge do valor do boleto.")
    _exigir(vencimento in decodificado["vencimentos_possiveis"],
            "O fator de vencimento da linha digitável diverge do vencimento do boleto.")

    texto_instrucoes = "\n".join(instrucoes)
    encargos = []
    multa = re.search(rf"Multa\s+([\d,]+)%\s*=\s*R\$\s*({VALOR})", texto_instrucoes)
    if multa:
        encargos.append({"tipo": "MULTA", "percentual": _dinheiro(multa[1]), "valor_fixo": _dinheiro(multa[2]),
                         "periodicidade": "UNICA", "inicio_aplicacao": vencimento + timedelta(days=1),
                         "descricao": multa[0]})
    juros = re.search(rf"Juros\s+([\d,]+)%\s*a\.d\.\s*=\s*R\$\s*({VALOR})/dia", texto_instrucoes)
    if juros:
        encargos.append({"tipo": "JUROS", "percentual": _dinheiro(juros[1]), "valor_fixo": _dinheiro(juros[2]),
                         "periodicidade": "DIARIA", "inicio_aplicacao": vencimento + timedelta(days=1),
                         "descricao": juros[0]})
    desconto = re.search(rf"Até dia (\d{{2}}/\d{{2}}/\d{{4}}) conceder desconto de R\$\s*({VALOR}),"
                         rf" cobrar R\$\s*({VALOR})", texto_instrucoes)
    if desconto:
        _exigir(valor - _dinheiro(desconto[2]) == _dinheiro(desconto[3]),
                "O valor com desconto informado nas instruções não confere.")
        encargos.append({"tipo": "DESCONTO", "percentual": None, "valor_fixo": _dinheiro(desconto[2]),
                         "periodicidade": None, "inicio_aplicacao": None, "descricao": desconto[0]})
    email = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", texto_instrucoes)
    telefone = re.search(r"\(\d{2}\)\s*\d{4,5}-?\d{4}", texto_instrucoes)

    pagador = {
        "nome": pagador_nome, "cpf_mascarado": pagador_cpf,
        "endereco": {"logradouro": _normalizar(pagador_endereco[1]), "unidade": pagador_endereco[2],
                     "bairro": _normalizar(pagador_endereco[3]), "cep": re.sub(r"\D", "", pagador_cidade[1]),
                     "cidade": _normalizar(pagador_cidade[2]), "uf": pagador_cidade[3],
                     "linhas_pdf": rodape_ficha[:2]},
    }
    cnpj = re.sub(r"\D", "", beneficiario[2])
    return {
        "condominio": {"nome": beneficiario[1], "razao_social": beneficiario[1], "cnpj": cnpj,
                       "logradouro": endereco[1].strip(), "numero": endereco[2], "complemento": "",
                       "bairro": endereco[3].strip(), "cidade": endereco[4].strip(), "uf": endereco[5].upper(),
                       "cep": re.sub(r"\D", "", endereco[6]), "email": "", "telefone": ""},
        "unidade": unidade,
        "pagador": pagador,
        "encargos": encargos,
        "boleto": {
            "competencia": datetime.strptime(competencia, "%m/%Y").date(),
            "data_emissao": _data(datas[1]), "data_processamento": _data(datas[3]),
            "data_vencimento": vencimento, "data_pagamento": None,
            "numero_documento": documento, "nosso_numero": nosso_numero,
            "linha_digitavel": linha_digitavel, "codigo_barras": decodificado["codigo_barras"],
            "moeda": "BRL", "valor_original": valor,
            "valor_desconto": _dinheiro(desconto[2]) if desconto else Decimal("0.00"),
            "data_limite_desconto": _data(desconto[1]) if desconto else None,
            "valor_pago": None, "status": "PENDENTE", "instrucoes": texto_instrucoes,
            "dados_extras": _json({
                "via": via[1], "emitido_em": datetime.strptime(f"{via[2]} {via[3]}", "%d/%m/%Y %H:%M"),
                "emitente_cabecalho": emitente_cabecalho,
                "banco": {"codigo": banco[1], "local_pagamento": linhas[i_local]},
                "agencia_codigo_beneficiario": agencia, "carteira": carteira, "especie": especie,
                "aceite": datas[2], "fator_vencimento": decodificado["fator_vencimento"],
                "valor_com_desconto": _dinheiro(desconto[3]) if desconto else None,
                "beneficiario": {"nome": beneficiario[1], "cnpj": cnpj, "endereco": beneficiario[3],
                                 "texto_recibo": beneficiario_recibo, "texto_ficha": beneficiario_ficha},
                "sacador_avalista": {"nome": _normalizar(sacador[1]), "cnpj": re.sub(r"\D", "", sacador[2])},
                "pagador": pagador,
                "contato_instrucoes": {"email": email[0] if email else None,
                                       "telefone": telefone[0] if telefone else None},
            }),
        },
    }


def _ler_composicao(texto: str, valor: Decimal) -> tuple[list, list]:
    linhas = texto.splitlines()
    fim = next((i for i, linha in enumerate(linhas) if re.fullmatch(r"_{5,}", linha.strip())), None)
    _exigir(fim is not None, "Fim da composição da cobrança não encontrado no PDF.")
    itens = []
    for bruta in linhas[:fim]:
        texto_linha = bruta.strip()
        if not texto_linha:
            continue
        item = re.fullmatch(rf"(.+?)\s{{2,}}({VALOR})", texto_linha)
        parcela = re.fullmatch(r"Parc\.\s*(\d+)/(\d+)", texto_linha)
        if item:
            itens.append({"ordem": len(itens) + 1, "categoria_original": None, "descricao": item[1].strip(),
                          "valor": _dinheiro(item[2]), "parcela_atual": None, "total_parcelas": None,
                          "dados_extras": {"linhas_pdf": [bruta.rstrip()]}})
        elif parcela and itens and itens[-1]["parcela_atual"] is None:
            itens[-1].update(parcela_atual=int(parcela[1]), total_parcelas=int(parcela[2]))
            itens[-1]["dados_extras"]["linhas_pdf"].append(bruta.rstrip())
        else:
            raise ValueError(f"Linha não reconhecida na composição da cobrança: {texto_linha}")
    _exigir(itens and sum(item["valor"] for item in itens) == valor,
            "A soma dos itens da composição não corresponde ao valor do boleto.")
    return itens, linhas[fim + 1:]


def _ler_correspondencia(linhas: list[str], vencimento: date, unidade: str, pagador: str) -> dict:
    conteudo = [linha.strip() for linha in linhas if linha.strip()]
    _exigir("Para uso dos correios" in conteudo, "Bloco de correspondência não reconhecido no PDF.")
    antes = conteudo[:conteudo.index("Para uso dos correios")]
    i_venc = next((i for i, linha in enumerate(antes) if linha.startswith("Vencimento:")), None)
    _exigir(i_venc is not None and i_venc >= 4, "Vencimento da correspondência não identificado no PDF.")
    _exigir(_data(antes[i_venc].split(":", 1)[1].strip()) == vencimento,
            "O vencimento da correspondência diverge do boleto.")
    _exigir(antes[i_venc - 3] == unidade, "A unidade da correspondência diverge do boleto.")
    _exigir(_chave(antes[0]) == _chave(pagador), "O destinatário da correspondência diverge do pagador.")
    marcador = "Registrado ao serviço postal em"
    _exigir(marcador in conteudo, "Remetente da correspondência não identificado no PDF.")
    remetente = conteudo[conteudo.index(marcador) + 1:]
    texto_remetente = " ".join(remetente)
    email = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", texto_remetente)
    telefone = re.search(r"\(\d{2}\)\s*\d{4,5}-?\d{4}", texto_remetente)
    return {
        "destinatario": antes[:i_venc - 3], "unidade": antes[i_venc - 3], "local_entrega": antes[i_venc - 2],
        "identificacao": antes[i_venc - 1], "vencimento": vencimento.isoformat(),
        "remetente": {"linhas": remetente, "email": email[0] if email else None,
                      "telefone": telefone[0] if telefone else None},
    }


def _separar_secoes(fluxo: list[tuple[int, str]]) -> tuple[dict, list]:
    secoes, rodape, atual = {}, [], None
    for pagina, bruta in fluxo:
        texto = bruta.strip()
        if texto in TITULOS_SECOES:
            atual = TITULOS_SECOES[texto]
            _exigir(atual not in secoes, f"Seção repetida no PDF: {texto}")
            secoes[atual] = []
        elif re.fullmatch(r"_{5,}", texto):
            atual = None
        elif re.fullmatch(r"\d{8} \d{4}", texto):
            rodape.append(texto)
        elif texto:
            _exigir(atual is not None, f"Conteúdo não reconhecido fora das seções do PDF: {texto}")
            secoes[atual].append((pagina, bruta.rstrip()))
    return secoes, rodape


def _ler_demonstrativo(linhas: list[tuple[int, str]]) -> dict:
    _exigir(linhas, "Demonstrativo de receitas e despesas vazio.")
    periodo = re.fullmatch(r"([A-Za-zÇç]{3})/(\d{4})(?:;\s*(.+))?", linhas[0][1].strip())
    _exigir(periodo, "Competência do demonstrativo não identificada no PDF.")
    competencia = _competencia_texto(f"{periodo[1]}/{periodo[2]}")
    linha_valor = re.compile(rf"^( *)(\S.*?)\s+(?:({VALOR})%\s+)?({VALOR})$")
    saldos, movimento, totais, possui_percentual = [], None, {}, False
    lancamentos, categorias, pilha, secao = [], {}, [], None

    def registrar_categoria(nome: str, natureza: str, nivel: int, pai: str | None) -> str:
        nome_normalizado = _normalizar(nome).upper()
        codigo = f"{pai} > {nome_normalizado}" if pai else nome_normalizado
        categorias.setdefault((natureza, codigo), {"natureza": natureza, "codigo": codigo, "nivel": nivel,
                                                   "nome_normalizado": nome_normalizado, "pai_codigo": pai})
        return codigo

    def lancar(pagina, rotulo, valor, percentual, totalizador, indentacao, categoria, linhas_pdf, grupo):
        lancamentos.append({
            "ordem": len(lancamentos) + 1, "descricao_original": rotulo, "natureza": secao["natureza"],
            "valor": valor, "percentual": _dinheiro(percentual) if percentual else None,
            "eh_totalizador": totalizador, "nivel_original": indentacao, "categoria_codigo": categoria,
            "dados_extras": {"pagina": pagina, "linhas_pdf": linhas_pdf, "grupo": grupo},
        })

    restantes = linhas[1:]
    posicao = 0
    while posicao < len(restantes):
        pagina, bruta = restantes[posicao]
        posicao += 1
        texto = bruta.strip()
        if set(texto) == {"-"}:
            continue
        if re.fullmatch(r"(%\s+)?VALOR", texto):
            possui_percentual = texto.startswith("%")
            continue
        if saldo := re.fullmatch(rf"saldo em (\d{{2}}/\d{{2}}/\d{{4}})\s+({VALOR})", texto, re.IGNORECASE):
            _exigir(not pilha and (secao is None or secao["fechada"]), f"Saldo fora de posição: {texto}")
            saldos.append({"data": _data(saldo[1]), "valor": _dinheiro(saldo[2]), "linha_pdf": bruta})
            continue
        if mov := re.fullmatch(rf"mov\. líquido \(receitas-despesas\)\s+({VALOR})", texto, re.IGNORECASE):
            movimento = _dinheiro(mov[1])
            continue
        if texto.upper() in ("RECEITAS", "DESPESAS") and not bruta.startswith(" "):
            _exigir(secao is None or secao["fechada"], f"Seção aberta sem total antes de {texto}.")
            natureza = "RECEITA" if texto.upper() == "RECEITAS" else "DESPESA"
            _exigir(natureza not in totais, f"Seção repetida no demonstrativo: {texto}")
            secao = {"nome": texto, "natureza": natureza, "indentacao": 0, "nivel": 1, "soma": Decimal("0"),
                     "fechada": False, "codigo": registrar_categoria(texto, natureza, 1, None)}
            continue
        _exigir(secao is not None and not secao["fechada"], f"Linha fora de receitas ou despesas: {texto}")
        achado = linha_valor.match(bruta)
        pai = pilha[-1] if pilha else secao
        if achado is None:
            indentacao = len(bruta) - len(bruta.lstrip(" "))
            _exigir(indentacao > pai["indentacao"], f"Linha não reconhecida no demonstrativo: {texto}")
            pilha.append({"nome": texto, "indentacao": indentacao, "nivel": pai["nivel"] + 1, "soma": Decimal("0"),
                          "codigo": registrar_categoria(texto, secao["natureza"], pai["nivel"] + 1, pai["codigo"])})
            continue
        indentacao, rotulo, percentual, valor = len(achado[1]), achado[2].strip(), achado[3], _dinheiro(achado[4])
        linhas_pdf = [bruta]
        if _chave(rotulo).startswith("total de "):
            alvo = pai if indentacao > 0 else secao
            _exigir(alvo is secao or pilha, f"Totalizador sem grupo correspondente: {texto}")
            esperado = "total de " + _chave(alvo["nome"])
            if _chave(rotulo) != esperado:
                _exigir(esperado.startswith(_chave(rotulo)) and posicao < len(restantes),
                        f"Totalizador não corresponde ao grupo '{alvo['nome']}': {texto}")
                restante = esperado[len(_chave(rotulo)):]
                continuacao = restantes[posicao][1]
                _exigir(_chave(continuacao) == restante.strip(), f"Continuação do totalizador não reconhecida: {texto}")
                posicao += 1
                rotulo += (" " if restante.startswith(" ") else "") + continuacao.strip()
                linhas_pdf.append(continuacao)
            _exigir(alvo["soma"] == valor,
                    f"A soma de '{alvo['nome']}' ({alvo['soma']}) difere do total informado ({valor}).")
            if alvo is secao:
                _exigir(not pilha, f"Grupo sem total antes de {texto}.")
                totais[secao["natureza"]] = valor
                secao["fechada"] = True
                grupo = None
            else:
                pilha.pop()
                (pilha[-1] if pilha else secao)["soma"] += valor
                grupo = (pilha[-1] if pilha else secao)["codigo"]
            lancar(pagina, rotulo, valor, percentual, True, indentacao, alvo["codigo"], linhas_pdf, grupo)
        else:
            _exigir(indentacao > pai["indentacao"], f"Lançamento fora de grupo: {texto}")
            categoria = registrar_categoria(rotulo, secao["natureza"], pai["nivel"] + 1, pai["codigo"])
            pai["soma"] += valor
            lancar(pagina, rotulo, valor, percentual, False, indentacao, categoria, linhas_pdf, pai["codigo"])

    _exigir(secao is not None and secao["fechada"] and not pilha, "Demonstrativo incompleto no PDF.")
    _exigir(set(totais) == {"RECEITA", "DESPESA"}, "Total de receitas ou de despesas ausente no demonstrativo.")
    _exigir(len(saldos) == 2 and movimento is not None, "Saldos ou movimento líquido ausentes no demonstrativo.")
    _exigir(totais["RECEITA"] - totais["DESPESA"] == movimento,
            "O movimento líquido não corresponde a receitas menos despesas.")
    _exigir(saldos[0]["valor"] + movimento == saldos[1]["valor"],
            "O saldo final não corresponde ao saldo inicial mais o movimento líquido.")
    periodo_inicio, periodo_fim = saldos[0]["data"] + timedelta(days=1), saldos[1]["data"]
    _exigir(periodo_inicio == competencia and periodo_fim.replace(day=1) == competencia,
            "O período dos saldos diverge da competência do demonstrativo.")
    return {
        "competencia": competencia, "periodo_inicio": periodo_inicio, "periodo_fim": periodo_fim,
        "saldo_inicial": saldos[0]["valor"], "total_receitas": totais["RECEITA"],
        "total_despesas": totais["DESPESA"], "movimento_liquido": movimento, "saldo_final": saldos[1]["valor"],
        "lancamentos": lancamentos,
        "categorias": sorted(categorias.values(), key=lambda item: (item["nivel"], item["codigo"])),
        "dados_extras": {"titulo": "DEMONSTRATIVO DE RECEITAS E DESPESAS", "periodo_pdf": linhas[0][1].strip(),
                         "agrupamento": periodo[3], "possui_percentual": possui_percentual,
                         "saldo_inicial_em": saldos[0]["data"], "saldo_final_em": saldos[1]["data"],
                         "linhas_saldo_pdf": [saldo["linha_pdf"] for saldo in saldos]},
    }


def _juntar_nome_conta(partes: list[str]) -> str:
    nome = partes[0]
    for anterior, parte in zip(partes, partes[1:]):
        nome += parte if len(anterior) >= LARGURA_NOME_CONTA else " " + parte
    return _normalizar(nome)


def _ler_resumo(linhas: list[tuple[int, str]]) -> dict:
    _exigir(linhas, "Resumo financeiro vazio.")
    periodo = re.fullmatch(r"DE (\d{2}/\d{2}/\d{4}) ATÉ (\d{2}/\d{2}/\d{4})", linhas[0][1].strip(), re.IGNORECASE)
    _exigir(periodo, "Período do resumo financeiro não identificado no PDF.")
    linha_conta = re.compile(rf"^(\S.*?)\s+({VALOR})\s+({VALOR})\s*\(?({VALOR})\)?\s+({VALOR})$")
    contas, total, observacao, colunas = [], None, None, None
    for pagina, bruta in linhas[1:]:
        texto = bruta.strip()
        if re.match(r"CONTA\s+SALDO ANT\.", texto):
            colunas = _normalizar(texto)
            continue
        if set(texto) == {"-"}:
            continue
        if texto.startswith("(*)"):
            observacao = texto
            continue
        achado = linha_conta.match(bruta)
        if achado:
            saldo_anterior, creditos, debitos, saldo_final = (_dinheiro(achado[i]) for i in range(2, 6))
            _exigir(saldo_anterior + creditos - debitos == saldo_final,
                    f"Saldo da conta não confere no resumo financeiro: {texto}")
            registro = {"saldo_anterior": saldo_anterior, "creditos": creditos, "debitos": debitos,
                        "saldo_final": saldo_final, "linhas_pdf": [bruta], "pagina": pagina}
            if _chave(achado[1]) == "saldo final":
                _exigir(total is None, "Total do resumo financeiro repetido.")
                total = registro
            else:
                _exigir(total is None, f"Conta após o total do resumo financeiro: {texto}")
                contas.append(dict(registro, partes=[achado[1].rstrip()]))
            continue
        _exigir(contas and total is None, f"Linha não reconhecida no resumo financeiro: {texto}")
        contas[-1]["partes"].append(texto)
        contas[-1]["linhas_pdf"].append(bruta)
    _exigir(contas and total, "Contas ou total ausentes no resumo financeiro.")
    for campo in ("saldo_anterior", "creditos", "debitos", "saldo_final"):
        _exigir(sum(conta[campo] for conta in contas) == total[campo],
                f"A soma de '{campo}' das contas difere do total do resumo financeiro.")
    inclui_transferencias = bool(observacao and "transfer" in observacao.casefold())
    nomes = [_juntar_nome_conta(conta["partes"]) for conta in contas]
    return {
        "periodo_inicio": _data(periodo[1]), "periodo_fim": _data(periodo[2]),
        "contas": [{"nome": nome, "saldo_anterior": conta["saldo_anterior"], "creditos": conta["creditos"],
                    "debitos": conta["debitos"], "saldo_final": conta["saldo_final"],
                    "inclui_transferencias": inclui_transferencias}
                   for nome, conta in zip(nomes, contas)],
        "dados_extras": {"titulo": "RESUMO FINANCEIRO", "periodo_pdf": linhas[0][1].strip(), "colunas": colunas,
                         "total": {campo: total[campo] for campo in ("saldo_anterior", "creditos", "debitos", "saldo_final")},
                         "linha_total_pdf": total["linhas_pdf"][0], "observacao": observacao,
                         "contas_pdf": [{"nome": nome, "pagina": conta["pagina"], "linhas_pdf": conta["linhas_pdf"]}
                                        for nome, conta in zip(nomes, contas)]},
    }


def _ler_inadimplencia(linhas: list[tuple[int, str]]) -> dict:
    meses, colunas, tabela, observacao = None, [], {}, None
    linha_tabela = re.compile(r"(BOLETOS|UNIDADES|VALOR\*?)\s+(\S+)\s+-\s+(\S+)\s+\+\s+(\S+)\s+=\s+(\S+)")
    for _, bruta in linhas:
        texto = bruta.strip()
        tokens = re.findall(r"\b([A-Z]{3}/\d{2})\b", texto)
        if meses is None and len(tokens) == 2:
            meses = tokens
            colunas.append(_normalizar(texto))
        elif set(texto) == {"-"}:
            continue
        elif texto.startswith("*"):
            observacao = texto
        elif achado := linha_tabela.fullmatch(texto):
            celulas = []
            for celula in achado.groups()[1:]:
                quantidade = re.fullmatch(r"(\d+)(?:\((\d+(?:,\d+)?)%\))?", celula)
                if achado[1].startswith("VALOR"):
                    _exigir(re.fullmatch(VALOR, celula), f"Valor inválido na inadimplência: {texto}")
                    celulas.append({"valor": _dinheiro(celula)})
                else:
                    _exigir(quantidade, f"Quantidade inválida na inadimplência: {texto}")
                    celulas.append({"quantidade": int(quantidade[1]),
                                    "percentual": Decimal(quantidade[2].replace(",", ".")) if quantidade[2] else None})
            chave = "valor" if achado[1].startswith("VALOR") else "quantidade"
            anterior, regularizadas, novas, atual = (celula[chave] for celula in celulas)
            _exigir(anterior - regularizadas + novas == atual, f"Linha de inadimplência não confere: {texto}")
            tabela[achado[1].rstrip("*").lower()] = {"anterior": celulas[0], "regularizadas": celulas[1],
                                                     "novas": celulas[2], "atual": celulas[3], "linha_pdf": bruta}
        else:
            _exigir(meses is not None and not tabela, f"Linha não reconhecida na inadimplência: {texto}")
            colunas.append(_normalizar(texto))
    _exigir(meses and set(tabela) == {"boletos", "unidades", "valor"}, "Tabela de inadimplência incompleta no PDF.")
    return {
        "competencia": _competencia_texto(meses[1]),
        "boletos_abertos": tabela["boletos"]["atual"]["quantidade"],
        "unidades_inadimplentes": tabela["unidades"]["atual"]["quantidade"],
        "percentual_unidades": tabela["unidades"]["atual"]["percentual"],
        "valor_sem_acrescimos": tabela["valor"]["atual"]["valor"],
        "regularizados_periodo": tabela["boletos"]["regularizadas"]["quantidade"],
        "novos_periodo": tabela["boletos"]["novas"]["quantidade"],
        "dados_extras": _json({"titulo": "SALDO DA INADIMPLÊNCIA", "competencia_anterior": meses[0],
                               "competencia_atual": meses[1], "colunas": colunas, "tabela": tabela,
                               "observacao": observacao}),
    }


def ler_boleto_pdf(conteudo: bytes, nome_arquivo: str) -> dict:
    _exigir(conteudo.startswith(b"%PDF-"), "O arquivo não é um PDF válido.")
    reader = PdfReader(BytesIO(conteudo))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF protegido por senha.")
    paginas = [page.extract_text() or "" for page in reader.pages]
    _exigir(any(texto.strip() for texto in paginas), "PDF sem texto. É necessário OCR para arquivos digitalizados.")
    cabecalho, marcador, restante = paginas[0].partition("Composição da cobrança")
    _exigir(marcador, "Seção 'Composição da cobrança' não encontrada no PDF.")
    leitura = _ler_cabecalho(cabecalho)
    boleto = leitura["boleto"]
    itens, sobra_pagina1 = _ler_composicao(restante, boleto["valor_original"])

    fluxo, correspondencia = [(1, linha) for linha in sobra_pagina1], None
    for numero, texto in enumerate(paginas[1:], start=2):
        linhas = texto.splitlines()
        marcas = [i for i, linha in enumerate(linhas) if linha.strip() == "Remetente"]
        if marcas:
            _exigir(correspondencia is None, "Mais de um bloco de correspondência no PDF.")
            correspondencia = _ler_correspondencia(linhas[:marcas[-1]], boleto["data_vencimento"],
                                                   leitura["unidade"], leitura["pagador"]["nome"])
            correspondencia["pagina"] = numero
            linhas = linhas[marcas[-1] + 1:]
        fluxo.extend((numero, linha) for linha in linhas)
    secoes, rodape = _separar_secoes(fluxo)
    _exigir("demonstrativo" in secoes, "Demonstrativo de receitas e despesas não encontrado no PDF.")
    demonstrativo = _ler_demonstrativo(secoes["demonstrativo"])
    demonstrativo["contas"], demonstrativo["inadimplencia"] = [], None
    if "resumo" in secoes:
        resumo = _ler_resumo(secoes["resumo"])
        total = resumo["dados_extras"]["total"]
        _exigir(resumo["periodo_inicio"] == demonstrativo["periodo_inicio"]
                and resumo["periodo_fim"] == demonstrativo["periodo_fim"],
                "O período do resumo financeiro diverge do demonstrativo.")
        _exigir(total["saldo_anterior"] == demonstrativo["saldo_inicial"]
                and total["saldo_final"] == demonstrativo["saldo_final"],
                "Os saldos do resumo financeiro divergem do demonstrativo.")
        demonstrativo["contas"] = resumo["contas"]
        demonstrativo["dados_extras"]["resumo_financeiro"] = resumo["dados_extras"]
    if "inadimplencia" in secoes:
        demonstrativo["inadimplencia"] = _ler_inadimplencia(secoes["inadimplencia"])
        _exigir(demonstrativo["inadimplencia"]["competencia"] == demonstrativo["competencia"],
                "A competência da inadimplência diverge do demonstrativo.")

    sha256 = hashlib.sha256(conteudo).hexdigest()
    demonstrativo["dados_extras"] = _json(demonstrativo["dados_extras"])
    for lancamento in demonstrativo["lancamentos"]:
        lancamento["dados_extras"] = _json(lancamento["dados_extras"])
    boleto["dados_extras"].update(_json({
        "arquivo_origem": nome_arquivo, "sha256": sha256, "correspondencia": correspondencia,
        "rodape": rodape, "demonstrativo_competencia": demonstrativo["competencia"],
    }))
    metadados_pdf = {str(chave): str(valor) for chave, valor in (reader.metadata or {}).items()}
    return {
        "documento": {"nome_arquivo": nome_arquivo, "mime_type": "application/pdf", "tamanho_bytes": len(conteudo),
                      "hash_sha256": sha256, "local_armazenamento": "nao_armazenado",
                      "motor_extracao": "pypdf", "versao_motor": pypdf.__version__,
                      "metadados": {"tipo_documento": "BOLETO_CONDOMINIO", "numero_paginas": len(paginas),
                                    "arquivo_original_armazenado": False, "pdf": metadados_pdf}},
        "paginas": [{"numero_pagina": numero, "texto_extraido": texto,
                     "dados_brutos": {"linhas": len(texto.splitlines()), "caracteres": len(texto)}}
                    for numero, texto in enumerate(paginas, start=1)],
        "condominio": leitura["condominio"],
        "unidade": leitura["unidade"],
        "pagador": leitura["pagador"],
        "boleto": boleto,
        "itens": itens,
        "encargos": leitura["encargos"],
        "demonstrativo": demonstrativo,
    }