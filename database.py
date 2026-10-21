"""Camada de acesso ao banco de dados de boletos e demonstrativos condominiais (PostgreSQL/Neon).

Usa o schema "condominio" criado pelo script modelo_condominios_postgresql.sql.
"""
import json
from datetime import date

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

STATUS_BOLETO = ["PENDENTE", "PAGO", "VENCIDO", "CANCELADO", "RENEGOCIADO"]


@st.cache_resource
def get_engine() -> Engine:
    database_url = st.secrets["database_url"]
    # força o driver psycopg2, já que o sqlalchemy 2.x usa psycopg (v3) por padrão
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(database_url, pool_pre_ping=True)

    @event.listens_for(engine, "begin")
    def _set_transaction_schema(connection):
        connection.exec_driver_sql("SET LOCAL search_path TO condominio, public")

    return engine


def only_digits(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


# ---------- Condomínios ----------

def add_condominio(
    nome: str, razao_social: str, cnpj: str, logradouro: str, numero: str, complemento: str,
    bairro: str, cidade: str, uf: str, cep: str, email: str, telefone: str,
) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO condominio.condominio (
                    nome, razao_social, cnpj, logradouro, numero, complemento,
                    bairro, cidade, uf, cep, email, telefone
                ) VALUES (
                    :nome, :razao_social, :cnpj, :logradouro, :numero, :complemento,
                    :bairro, :cidade, :uf, :cep, :email, :telefone
                )
            """),
            {
                "nome": nome, "razao_social": razao_social or None, "cnpj": cnpj or None,
                "logradouro": logradouro or None, "numero": numero or None, "complemento": complemento or None,
                "bairro": bairro or None, "cidade": cidade or None, "uf": uf or None, "cep": cep or None,
                "email": email or None, "telefone": telefone or None,
            },
        )


def get_condominios() -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(text("""
        SELECT id, nome, razao_social, cnpj, logradouro, numero, complemento,
               bairro, cidade, uf, cep, email, telefone, ativo
        FROM condominio.condominio ORDER BY nome
    """), engine)


def update_condominio(
    condominio_id: int, nome: str, razao_social: str, cnpj: str, logradouro: str, numero: str,
    complemento: str, bairro: str, cidade: str, uf: str, cep: str, email: str, telefone: str, ativo: bool,
) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE condominio.condominio
                SET nome = :nome, razao_social = :razao_social, cnpj = :cnpj, logradouro = :logradouro,
                    numero = :numero, complemento = :complemento, bairro = :bairro, cidade = :cidade,
                    uf = :uf, cep = :cep, email = :email, telefone = :telefone, ativo = :ativo
                WHERE id = :condominio_id
            """),
            {
                "nome": nome, "razao_social": razao_social or None, "cnpj": cnpj or None,
                "logradouro": logradouro or None, "numero": numero or None, "complemento": complemento or None,
                "bairro": bairro or None, "cidade": cidade or None, "uf": uf or None, "cep": cep or None,
                "email": email or None, "telefone": telefone or None, "ativo": ativo, "condominio_id": condominio_id,
            },
        )


def delete_condominio(condominio_id: int) -> None:
    """Remove o condomínio. Falha se houver boletos vinculados (restrição do banco)."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM condominio.condominio WHERE id = :condominio_id"), {"condominio_id": condominio_id})


# ---------- Unidades ----------

def add_unidade(condominio_id: int, codigo: str, bloco: str, andar: str, fracao_ideal: float | None, observacoes: str) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO condominio.unidade (condominio_id, codigo, bloco, andar, fracao_ideal, observacoes)
                VALUES (:condominio_id, :codigo, :bloco, :andar, :fracao_ideal, :observacoes)
            """),
            {
                "condominio_id": condominio_id, "codigo": codigo, "bloco": bloco or "",
                "andar": andar or None, "fracao_ideal": fracao_ideal, "observacoes": observacoes or None,
            },
        )


def get_unidades(condominio_id: int | None = None) -> pd.DataFrame:
    engine = get_engine()
    query = """
        SELECT u.id, u.codigo, u.bloco, u.andar, u.fracao_ideal, u.observacoes, u.ativo,
               u.condominio_id, c.nome AS condominio_nome
        FROM condominio.unidade u
        JOIN condominio.condominio c ON c.id = u.condominio_id
    """
    params = {}
    if condominio_id is not None:
        query += " WHERE u.condominio_id = :condominio_id"
        params["condominio_id"] = condominio_id
    query += " ORDER BY c.nome, u.bloco, u.codigo"
    return pd.read_sql(text(query), engine, params=params)


def update_unidade(unidade_id: int, codigo: str, bloco: str, andar: str, fracao_ideal: float | None, observacoes: str) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE condominio.unidade
                SET codigo = :codigo, bloco = :bloco, andar = :andar,
                    fracao_ideal = :fracao_ideal, observacoes = :observacoes
                WHERE id = :unidade_id
            """),
            {
                "codigo": codigo, "bloco": bloco or "", "andar": andar or None,
                "fracao_ideal": fracao_ideal, "observacoes": observacoes or None, "unidade_id": unidade_id,
            },
        )


def delete_unidade(unidade_id: int) -> None:
    """Remove a unidade. Falha se houver boletos vinculados (restrição do banco)."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM condominio.unidade WHERE id = :unidade_id"), {"unidade_id": unidade_id})


# ---------- Boletos ----------

def add_boleto(
    condominio_id: int, unidade_id: int, competencia: date,
    data_emissao: date | None, data_processamento: date | None, data_vencimento: date,
    data_pagamento: date | None, numero_documento: str, nosso_numero: str,
    linha_digitavel: str, codigo_barras: str, moeda: str,
    valor_original: float, valor_desconto: float, data_limite_desconto: date | None,
    valor_pago: float | None, status: str, instrucoes: str, dados_extras: dict,
) -> int:
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO condominio.boleto (
                    condominio_id, unidade_id, competencia, data_emissao, data_processamento,
                    data_vencimento, data_pagamento, numero_documento, nosso_numero,
                    linha_digitavel, codigo_barras, moeda, valor_original, valor_desconto,
                    data_limite_desconto, valor_pago, status, instrucoes, dados_extras
                ) VALUES (
                    :condominio_id, :unidade_id, :competencia, :data_emissao, :data_processamento,
                    :data_vencimento, :data_pagamento, :numero_documento, :nosso_numero,
                    :linha_digitavel, :codigo_barras, :moeda, :valor_original, :valor_desconto,
                    :data_limite_desconto, :valor_pago, :status, :instrucoes, CAST(:dados_extras AS JSONB)
                ) RETURNING id
            """),
            {
                "condominio_id": condominio_id, "unidade_id": unidade_id, "competencia": competencia,
                "data_emissao": data_emissao, "data_processamento": data_processamento,
                "data_vencimento": data_vencimento, "data_pagamento": data_pagamento,
                "numero_documento": numero_documento or None, "nosso_numero": nosso_numero or None,
                "linha_digitavel": linha_digitavel or None, "codigo_barras": codigo_barras or None,
                "moeda": moeda or "BRL", "valor_original": valor_original, "valor_desconto": valor_desconto,
                "data_limite_desconto": data_limite_desconto, "valor_pago": valor_pago,
                "status": status, "instrucoes": instrucoes or None, "dados_extras": json.dumps(dados_extras),
            },
        )
        return result.scalar_one()


def add_boleto_item(
    boleto_id: int, ordem: int, categoria_original: str, descricao: str, valor: float,
    parcela_atual: int | None, total_parcelas: int | None,
) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO condominio.boleto_item (boleto_id, ordem, categoria_original, descricao, valor, parcela_atual, total_parcelas)
                VALUES (:boleto_id, :ordem, :categoria_original, :descricao, :valor, :parcela_atual, :total_parcelas)
            """),
            {
                "boleto_id": boleto_id, "ordem": ordem, "categoria_original": categoria_original or None,
                "descricao": descricao, "valor": valor, "parcela_atual": parcela_atual, "total_parcelas": total_parcelas,
            },
        )


def get_boletos(condominio_id: int | None = None) -> pd.DataFrame:
    engine = get_engine()
    query = "SELECT * FROM condominio.vw_boleto_resumo"
    params = {}
    if condominio_id is not None:
        query += " WHERE condominio = (SELECT nome FROM condominio.condominio WHERE id = :condominio_id)"
        params["condominio_id"] = condominio_id
    query += " ORDER BY competencia DESC, data_vencimento DESC"
    return pd.read_sql(text(query), engine, params=params)


def salvar_boleto_pdf(condominio_id: int, unidade_id: int, leitura: dict) -> dict:
    engine = get_engine()
    with engine.begin() as conn:
        return importar_boleto_pdf(conn, condominio_id, unidade_id, leitura)


# Chaves gravadas pela primeira versão do importador; o texto agora fica em extracao_pagina.
CHAVES_LEGADAS_BOLETO = ["texto_pdf", "numero_paginas", "beneficiario_pdf", "cnpj_pdf"]


def _jsonb(valor) -> str:
    return json.dumps(valor, ensure_ascii=False)


def importar_boleto_pdf(conn, condominio_id: int, unidade_id: int, leitura: dict) -> dict:
    """Grava todos os dados lidos do PDF na transação de `conn`; reimportar atualiza sem duplicar."""
    boleto, documento, demonstrativo = leitura["boleto"], leitura["documento"], leitura["demonstrativo"]
    vinculo = conn.execute(text("""
        SELECT u.codigo, c.cnpj
        FROM condominio.unidade u
        JOIN condominio.condominio c ON c.id = u.condominio_id
        WHERE u.id = :unidade_id AND c.id = :condominio_id
    """), {"unidade_id": unidade_id, "condominio_id": condominio_id}).mappings().one_or_none()
    if vinculo is None or vinculo["codigo"] != leitura["unidade"]:
        raise ValueError("A unidade selecionada nao corresponde ao boleto ou ao condominio.")
    if vinculo["cnpj"] and vinculo["cnpj"] != leitura["condominio"]["cnpj"]:
        raise ValueError("O CNPJ do boleto nao corresponde ao condominio selecionado.")
    pagador = leitura["pagador"]
    for chave in (f"boleto:{condominio_id}:{boleto['numero_documento']}",
                  f"demonstrativo:{condominio_id}:{demonstrativo['competencia']}",
                  f"pessoa:{pagador['nome']}:{pagador['cpf_mascarado']}"):
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:chave))"), {"chave": chave})

    anterior = conn.execute(text("""
        SELECT condominio_id FROM condominio.documento_importado WHERE hash_sha256 = :hash_sha256
    """), {"hash_sha256": documento["hash_sha256"]}).mappings().one_or_none()
    if anterior is not None and anterior["condominio_id"] not in (None, condominio_id):
        raise ValueError("Este PDF ja foi importado para outro condominio.")
    documento_id = conn.execute(text("""
        INSERT INTO condominio.documento_importado (
            condominio_id, nome_arquivo, mime_type, tamanho_bytes, hash_sha256, local_armazenamento,
            status, motor_extracao, versao_motor, metadados, processado_em, mensagem_erro
        ) VALUES (
            :condominio_id, :nome_arquivo, :mime_type, :tamanho_bytes, :hash_sha256, :local_armazenamento,
            'PROCESSADO', :motor_extracao, :versao_motor, CAST(:metadados AS JSONB), now(), NULL
        )
        ON CONFLICT (hash_sha256) DO UPDATE SET
            condominio_id = EXCLUDED.condominio_id, nome_arquivo = EXCLUDED.nome_arquivo,
            mime_type = EXCLUDED.mime_type, tamanho_bytes = EXCLUDED.tamanho_bytes,
            local_armazenamento = EXCLUDED.local_armazenamento, status = EXCLUDED.status,
            motor_extracao = EXCLUDED.motor_extracao, versao_motor = EXCLUDED.versao_motor,
            metadados = EXCLUDED.metadados, processado_em = EXCLUDED.processado_em, mensagem_erro = NULL
        RETURNING id
    """), {
        "condominio_id": condominio_id, "nome_arquivo": documento["nome_arquivo"],
        "mime_type": documento["mime_type"], "tamanho_bytes": documento["tamanho_bytes"],
        "hash_sha256": documento["hash_sha256"], "local_armazenamento": documento["local_armazenamento"],
        "motor_extracao": documento["motor_extracao"], "versao_motor": documento["versao_motor"],
        "metadados": _jsonb(documento["metadados"]),
    }).scalar_one()
    for pagina in leitura["paginas"]:
        conn.execute(text("""
            INSERT INTO condominio.extracao_pagina (documento_id, numero_pagina, texto_extraido, dados_brutos)
            VALUES (:documento_id, :numero_pagina, :texto_extraido, CAST(:dados_brutos AS JSONB))
            ON CONFLICT (documento_id, numero_pagina) DO UPDATE SET
                texto_extraido = EXCLUDED.texto_extraido, dados_brutos = EXCLUDED.dados_brutos
        """), {"documento_id": documento_id, "numero_pagina": pagina["numero_pagina"],
               "texto_extraido": pagina["texto_extraido"], "dados_brutos": _jsonb(pagina["dados_brutos"])})

    demonstrativo_id, reaproveitado = _gravar_demonstrativo(
        conn, condominio_id, documento_id, documento["hash_sha256"], demonstrativo)

    pagador_id = conn.execute(text("""
        SELECT id FROM condominio.pessoa
        WHERE nome = :nome AND cpf_mascarado IS NOT DISTINCT FROM :cpf_mascarado
        ORDER BY id LIMIT 1
    """), {"nome": pagador["nome"], "cpf_mascarado": pagador["cpf_mascarado"]}).scalar_one_or_none()
    if pagador_id is None:
        pagador_id = conn.execute(text("""
            INSERT INTO condominio.pessoa (nome, cpf_mascarado) VALUES (:nome, :cpf_mascarado) RETURNING id
        """), {"nome": pagador["nome"], "cpf_mascarado": pagador["cpf_mascarado"]}).scalar_one()
    conn.execute(text("""
        INSERT INTO condominio.unidade_pessoa (unidade_id, pessoa_id, papel, inicio_vigencia, principal)
        SELECT :unidade_id, :pessoa_id, CAST('PAGADOR' AS condominio.tipo_pessoa_unidade), CAST(:inicio AS DATE),
               NOT EXISTS (
                   SELECT 1 FROM condominio.unidade_pessoa
                   WHERE unidade_id = :unidade_id AND papel = 'PAGADOR' AND principal AND fim_vigencia IS NULL
               )
        WHERE NOT EXISTS (
            SELECT 1 FROM condominio.unidade_pessoa
            WHERE unidade_id = :unidade_id AND pessoa_id = :pessoa_id AND papel = 'PAGADOR' AND fim_vigencia IS NULL
        )
        ON CONFLICT ON CONSTRAINT uq_unidade_pessoa DO NOTHING
    """), {"unidade_id": unidade_id, "pessoa_id": pagador_id, "inicio": boleto["data_emissao"]})

    existentes = conn.execute(text("""
        SELECT id, condominio_id, unidade_id FROM condominio.boleto
        WHERE (condominio_id = :condominio_id AND numero_documento = :numero_documento)
           OR linha_digitavel = :linha_digitavel
    """), {"condominio_id": condominio_id, "numero_documento": boleto["numero_documento"],
           "linha_digitavel": boleto["linha_digitavel"]}).mappings().all()
    if len(existentes) > 1:
        raise ValueError("Numero do documento e linha digitavel apontam para boletos diferentes.")
    if existentes and (existentes[0]["condominio_id"] != condominio_id or existentes[0]["unidade_id"] != unidade_id):
        raise ValueError("Este boleto ja esta registrado com outro condominio ou unidade.")
    campos = {
        "documento_id": documento_id, "pagador_id": pagador_id,
        "competencia": boleto["competencia"], "data_emissao": boleto["data_emissao"],
        "data_processamento": boleto["data_processamento"], "data_vencimento": boleto["data_vencimento"],
        "numero_documento": boleto["numero_documento"], "nosso_numero": boleto["nosso_numero"],
        "linha_digitavel": boleto["linha_digitavel"], "codigo_barras": boleto["codigo_barras"],
        "moeda": boleto["moeda"], "valor_original": boleto["valor_original"],
        "valor_desconto": boleto["valor_desconto"], "data_limite_desconto": boleto["data_limite_desconto"],
        "instrucoes": boleto["instrucoes"],
        "dados_extras": _jsonb(dict(boleto["dados_extras"], documento_id=documento_id,
                                    demonstrativo_id=demonstrativo_id)),
    }
    if existentes:
        boleto_id = existentes[0]["id"]
        # Status, data e valor de pagamento são mantidos: podem ter sido atualizados após a importação.
        conn.execute(text("""
            UPDATE condominio.boleto SET
                documento_id = :documento_id, pagador_id = :pagador_id, competencia = :competencia,
                data_emissao = :data_emissao, data_processamento = :data_processamento,
                data_vencimento = :data_vencimento, numero_documento = :numero_documento,
                nosso_numero = :nosso_numero, linha_digitavel = :linha_digitavel, codigo_barras = :codigo_barras,
                moeda = :moeda, valor_original = :valor_original, valor_desconto = :valor_desconto,
                data_limite_desconto = :data_limite_desconto, instrucoes = :instrucoes,
                dados_extras = (dados_extras - CAST(:chaves_legadas AS TEXT[])) || CAST(:dados_extras AS JSONB)
            WHERE id = :boleto_id
        """), dict(campos, boleto_id=boleto_id, chaves_legadas=CHAVES_LEGADAS_BOLETO))
    else:
        boleto_id = conn.execute(text("""
            INSERT INTO condominio.boleto (
                documento_id, condominio_id, unidade_id, pagador_id, competencia, data_emissao,
                data_processamento, data_vencimento, numero_documento, nosso_numero, linha_digitavel,
                codigo_barras, moeda, valor_original, valor_desconto, data_limite_desconto, status,
                instrucoes, dados_extras
            ) VALUES (
                :documento_id, :condominio_id, :unidade_id, :pagador_id, :competencia, :data_emissao,
                :data_processamento, :data_vencimento, :numero_documento, :nosso_numero, :linha_digitavel,
                :codigo_barras, :moeda, :valor_original, :valor_desconto, :data_limite_desconto, 'PENDENTE',
                :instrucoes, CAST(:dados_extras AS JSONB)
            ) RETURNING id
        """), dict(campos, condominio_id=condominio_id, unidade_id=unidade_id)).scalar_one()

    conn.execute(text("DELETE FROM condominio.boleto_item WHERE boleto_id = :boleto_id"), {"boleto_id": boleto_id})
    for item in leitura["itens"]:
        conn.execute(text("""
            INSERT INTO condominio.boleto_item (
                boleto_id, ordem, categoria_original, descricao, valor, parcela_atual, total_parcelas, dados_extras
            ) VALUES (
                :boleto_id, :ordem, :categoria_original, :descricao, :valor, :parcela_atual, :total_parcelas,
                CAST(:dados_extras AS JSONB)
            )
        """), {"boleto_id": boleto_id, "ordem": item["ordem"], "categoria_original": item["categoria_original"],
               "descricao": item["descricao"], "valor": item["valor"], "parcela_atual": item["parcela_atual"],
               "total_parcelas": item["total_parcelas"], "dados_extras": _jsonb(item["dados_extras"])})
    conn.execute(text("DELETE FROM condominio.boleto_encargo WHERE boleto_id = :boleto_id"), {"boleto_id": boleto_id})
    for encargo in leitura["encargos"]:
        conn.execute(text("""
            INSERT INTO condominio.boleto_encargo (
                boleto_id, tipo, percentual, valor_fixo, periodicidade, inicio_aplicacao, descricao
            ) VALUES (
                :boleto_id, :tipo, :percentual, :valor_fixo, :periodicidade, :inicio_aplicacao, :descricao
            )
        """), dict(encargo, boleto_id=boleto_id))

    return {
        "boleto_id": boleto_id, "boleto_novo": not existentes, "documento_id": documento_id,
        "pagador_id": pagador_id, "demonstrativo_id": demonstrativo_id,
        "demonstrativo_reaproveitado": reaproveitado, "itens": len(leitura["itens"]),
        "encargos": len(leitura["encargos"]), "lancamentos": len(demonstrativo["lancamentos"]),
        "categorias": len(demonstrativo["categorias"]), "contas": len(demonstrativo["contas"]),
        "inadimplencia": demonstrativo["inadimplencia"] is not None, "paginas": len(leitura["paginas"]),
    }


def _assinatura_demonstrativo(demonstrativo: dict) -> tuple:
    inadimplencia = demonstrativo["inadimplencia"]
    return (
        tuple(demonstrativo[campo] for campo in ("periodo_inicio", "periodo_fim", "saldo_inicial", "total_receitas",
                                                 "total_despesas", "movimento_liquido", "saldo_final")),
        tuple((l["ordem"], l["descricao_original"], l["natureza"], l["valor"], l["percentual"],
               l["eh_totalizador"], l["nivel_original"]) for l in demonstrativo["lancamentos"]),
        tuple(sorted((c["nome"], c["saldo_anterior"], c["creditos"], c["debitos"], c["saldo_final"])
                     for c in demonstrativo["contas"])),
        None if inadimplencia is None else tuple(inadimplencia[campo] for campo in (
            "competencia", "boletos_abertos", "unidades_inadimplentes", "percentual_unidades",
            "valor_sem_acrescimos", "regularizados_periodo", "novos_periodo")),
    )


def _carregar_demonstrativo(conn, demonstrativo_id: int) -> dict:
    cabecalho = conn.execute(text("""
        SELECT * FROM condominio.demonstrativo_mensal WHERE id = :id
    """), {"id": demonstrativo_id}).mappings().one()
    lancamentos = conn.execute(text("""
        SELECT ordem, descricao_original, natureza::text AS natureza, valor, percentual, eh_totalizador, nivel_original
        FROM condominio.lancamento_financeiro WHERE demonstrativo_id = :id ORDER BY ordem
    """), {"id": demonstrativo_id}).mappings().all()
    contas = conn.execute(text("""
        SELECT c.nome, s.saldo_anterior, s.creditos, s.debitos, s.saldo_final
        FROM condominio.saldo_conta_mensal s JOIN condominio.conta_condominio c ON c.id = s.conta_id
        WHERE s.demonstrativo_id = :id
    """), {"id": demonstrativo_id}).mappings().all()
    inadimplencia = conn.execute(text("""
        SELECT * FROM condominio.inadimplencia_mensal WHERE demonstrativo_id = :id
    """), {"id": demonstrativo_id}).mappings().one_or_none()
    return dict(cabecalho, lancamentos=[dict(l) for l in lancamentos], contas=[dict(c) for c in contas],
                inadimplencia=dict(inadimplencia) if inadimplencia else None)


def _gravar_demonstrativo(conn, condominio_id: int, documento_id: int, hash_sha256: str, demonstrativo: dict):
    existentes = conn.execute(text("""
        SELECT id, documento_id, dados_extras FROM condominio.demonstrativo_mensal
        WHERE condominio_id = :condominio_id AND competencia = :competencia ORDER BY id
    """), {"condominio_id": condominio_id, "competencia": demonstrativo["competencia"]}).mappings().all()
    proprio = next((d for d in existentes if d["documento_id"] == documento_id), None)
    if proprio is None and existentes:
        # O mesmo demonstrativo acompanha os boletos de todas as unidades: reaproveita se for idêntico.
        outro = existentes[0]
        if _assinatura_demonstrativo(_carregar_demonstrativo(conn, outro["id"])) != _assinatura_demonstrativo(demonstrativo):
            raise ValueError(
                f"Ja existe demonstrativo de {demonstrativo['competencia']:%m/%Y} para este condominio "
                "com conteudo diferente do PDF.")
        origens = list(outro["dados_extras"].get("documentos_origem", []))
        if hash_sha256 not in origens:
            conn.execute(text("""
                UPDATE condominio.demonstrativo_mensal SET dados_extras = CAST(:dados_extras AS JSONB) WHERE id = :id
            """), {"id": outro["id"], "dados_extras": _jsonb(dict(outro["dados_extras"],
                                                                     documentos_origem=origens + [hash_sha256]))})
        return outro["id"], True

    origens = list(proprio["dados_extras"].get("documentos_origem", [])) if proprio else []
    campos = {
        "condominio_id": condominio_id, "documento_id": documento_id, "competencia": demonstrativo["competencia"],
        "periodo_inicio": demonstrativo["periodo_inicio"], "periodo_fim": demonstrativo["periodo_fim"],
        "saldo_inicial": demonstrativo["saldo_inicial"], "total_receitas": demonstrativo["total_receitas"],
        "total_despesas": demonstrativo["total_despesas"], "movimento_liquido": demonstrativo["movimento_liquido"],
        "saldo_final": demonstrativo["saldo_final"],
        "dados_extras": _jsonb(dict(demonstrativo["dados_extras"],
                                    documentos_origem=origens + [hash_sha256] if hash_sha256 not in origens else origens)),
    }
    if proprio:
        demonstrativo_id = proprio["id"]
        conn.execute(text("""
            UPDATE condominio.demonstrativo_mensal SET
                periodo_inicio = :periodo_inicio, periodo_fim = :periodo_fim, saldo_inicial = :saldo_inicial,
                total_receitas = :total_receitas, total_despesas = :total_despesas,
                movimento_liquido = :movimento_liquido, saldo_final = :saldo_final,
                dados_extras = CAST(:dados_extras AS JSONB)
            WHERE id = :demonstrativo_id
        """), dict(campos, demonstrativo_id=demonstrativo_id))
        for tabela in ("lancamento_financeiro", "saldo_conta_mensal", "inadimplencia_mensal"):
            conn.execute(text(f"DELETE FROM condominio.{tabela} WHERE demonstrativo_id = :id"), {"id": demonstrativo_id})
    else:
        demonstrativo_id = conn.execute(text("""
            INSERT INTO condominio.demonstrativo_mensal (
                documento_id, condominio_id, competencia, periodo_inicio, periodo_fim, saldo_inicial,
                total_receitas, total_despesas, movimento_liquido, saldo_final, dados_extras
            ) VALUES (
                :documento_id, :condominio_id, :competencia, :periodo_inicio, :periodo_fim, :saldo_inicial,
                :total_receitas, :total_despesas, :movimento_liquido, :saldo_final, CAST(:dados_extras AS JSONB)
            ) RETURNING id
        """), campos).scalar_one()

    categorias = {}
    for categoria in demonstrativo["categorias"]:
        chave = (categoria["natureza"], categoria["codigo"])
        categorias[chave] = conn.execute(text("""
            INSERT INTO condominio.categoria_financeira (
                condominio_id, categoria_pai_id, natureza, codigo, nome_normalizado, nivel
            ) VALUES (:condominio_id, :categoria_pai_id, :natureza, :codigo, :nome_normalizado, :nivel)
            ON CONFLICT ON CONSTRAINT uq_categoria DO UPDATE SET
                categoria_pai_id = EXCLUDED.categoria_pai_id, nivel = EXCLUDED.nivel, ativo = TRUE
            RETURNING id
        """), {"condominio_id": condominio_id, "natureza": categoria["natureza"], "codigo": categoria["codigo"],
               "categoria_pai_id": categorias[(categoria["natureza"], categoria["pai_codigo"])]
               if categoria["pai_codigo"] else None,
               "nome_normalizado": categoria["nome_normalizado"], "nivel": categoria["nivel"]}).scalar_one()
    for lancamento in demonstrativo["lancamentos"]:
        conn.execute(text("""
            INSERT INTO condominio.lancamento_financeiro (
                demonstrativo_id, categoria_id, ordem, descricao_original, natureza, valor, percentual,
                eh_totalizador, nivel_original, dados_extras
            ) VALUES (
                :demonstrativo_id, :categoria_id, :ordem, :descricao_original, :natureza, :valor, :percentual,
                :eh_totalizador, :nivel_original, CAST(:dados_extras AS JSONB)
            )
        """), {"demonstrativo_id": demonstrativo_id,
               "categoria_id": categorias[(lancamento["natureza"], lancamento["categoria_codigo"])],
               "ordem": lancamento["ordem"], "descricao_original": lancamento["descricao_original"],
               "natureza": lancamento["natureza"], "valor": lancamento["valor"], "percentual": lancamento["percentual"],
               "eh_totalizador": lancamento["eh_totalizador"], "nivel_original": lancamento["nivel_original"],
               "dados_extras": _jsonb(lancamento["dados_extras"])})
    for conta in demonstrativo["contas"]:
        conta_id = conn.execute(text("""
            SELECT id FROM condominio.conta_condominio
            WHERE condominio_id = :condominio_id AND nome = :nome AND instituicao IS NULL
        """), {"condominio_id": condominio_id, "nome": conta["nome"]}).scalar_one_or_none()
        if conta_id is None:
            conta_id = conn.execute(text("""
                INSERT INTO condominio.conta_condominio (condominio_id, nome) VALUES (:condominio_id, :nome) RETURNING id
            """), {"condominio_id": condominio_id, "nome": conta["nome"]}).scalar_one()
        conn.execute(text("""
            INSERT INTO condominio.saldo_conta_mensal (
                demonstrativo_id, conta_id, saldo_anterior, creditos, debitos, saldo_final, inclui_transferencias
            ) VALUES (
                :demonstrativo_id, :conta_id, :saldo_anterior, :creditos, :debitos, :saldo_final, :inclui_transferencias
            )
        """), {"demonstrativo_id": demonstrativo_id, "conta_id": conta_id,
               "saldo_anterior": conta["saldo_anterior"], "creditos": conta["creditos"], "debitos": conta["debitos"],
               "saldo_final": conta["saldo_final"], "inclui_transferencias": conta["inclui_transferencias"]})
    inadimplencia = demonstrativo["inadimplencia"]
    if inadimplencia is not None:
        conn.execute(text("""
            INSERT INTO condominio.inadimplencia_mensal (
                demonstrativo_id, competencia, boletos_abertos, unidades_inadimplentes, percentual_unidades,
                valor_sem_acrescimos, regularizados_periodo, novos_periodo, dados_extras
            ) VALUES (
                :demonstrativo_id, :competencia, :boletos_abertos, :unidades_inadimplentes, :percentual_unidades,
                :valor_sem_acrescimos, :regularizados_periodo, :novos_periodo, CAST(:dados_extras AS JSONB)
            )
        """), dict(inadimplencia, demonstrativo_id=demonstrativo_id, dados_extras=_jsonb(inadimplencia["dados_extras"])))
    return demonstrativo_id, False


def get_boleto_encargos(boleto_id: int) -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(
        text("""
            SELECT tipo, percentual, valor_fixo, periodicidade, inicio_aplicacao, descricao
            FROM condominio.boleto_encargo WHERE boleto_id = :boleto_id ORDER BY id
        """),
        engine, params={"boleto_id": boleto_id},
    )


def get_boleto_detalhe(boleto_id: int) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        return dict(conn.execute(text("""
            SELECT b.*, p.nome AS pagador, p.cpf_mascarado AS pagador_cpf_mascarado,
                   d.nome_arquivo, d.hash_sha256, d.importado_em, d.processado_em
            FROM condominio.boleto b
            LEFT JOIN condominio.pessoa p ON p.id = b.pagador_id
            LEFT JOIN condominio.documento_importado d ON d.id = b.documento_id
            WHERE b.id = :boleto_id
        """), {"boleto_id": boleto_id}).mappings().one())


def get_boleto_itens(boleto_id: int) -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(
        text("""
            SELECT ordem, categoria_original, descricao, valor, parcela_atual, total_parcelas
            FROM condominio.boleto_item WHERE boleto_id = :boleto_id ORDER BY ordem
        """),
        engine, params={"boleto_id": boleto_id},
    )


# ---------- Demonstrativos mensais ----------

def add_demonstrativo(
    condominio_id: int, competencia: date, periodo_inicio: date | None, periodo_fim: date | None,
    saldo_inicial: float | None, total_receitas: float | None, total_despesas: float | None,
    movimento_liquido: float | None, saldo_final: float | None, dados_extras: dict,
) -> int:
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO condominio.demonstrativo_mensal (
                    condominio_id, competencia, periodo_inicio, periodo_fim, saldo_inicial,
                    total_receitas, total_despesas, movimento_liquido, saldo_final, dados_extras
                ) VALUES (
                    :condominio_id, :competencia, :periodo_inicio, :periodo_fim, :saldo_inicial,
                    :total_receitas, :total_despesas, :movimento_liquido, :saldo_final, CAST(:dados_extras AS JSONB)
                ) RETURNING id
            """),
            {
                "condominio_id": condominio_id, "competencia": competencia,
                "periodo_inicio": periodo_inicio, "periodo_fim": periodo_fim, "saldo_inicial": saldo_inicial,
                "total_receitas": total_receitas, "total_despesas": total_despesas,
                "movimento_liquido": movimento_liquido, "saldo_final": saldo_final,
                "dados_extras": json.dumps(dados_extras),
            },
        )
        return result.scalar_one()


def add_lancamento(
    demonstrativo_id: int, ordem: int, descricao_original: str, natureza: str, valor: float,
) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO condominio.lancamento_financeiro (demonstrativo_id, ordem, descricao_original, natureza, valor)
                VALUES (:demonstrativo_id, :ordem, :descricao_original, :natureza, :valor)
            """),
            {
                "demonstrativo_id": demonstrativo_id, "ordem": ordem,
                "descricao_original": descricao_original, "natureza": natureza, "valor": valor,
            },
        )


def get_demonstrativos(condominio_id: int | None = None) -> pd.DataFrame:
    engine = get_engine()
    query = "SELECT * FROM condominio.vw_demonstrativo_resumo"
    params = {}
    if condominio_id is not None:
        query += " WHERE condominio_id = :condominio_id"
        params["condominio_id"] = condominio_id
    query += " ORDER BY competencia DESC"
    return pd.read_sql(text(query), engine, params=params)


def get_lancamentos(demonstrativo_id: int) -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(
        text("""
            SELECT l.ordem, l.natureza::text AS natureza, l.nivel_original, l.eh_totalizador,
                   l.descricao_original, l.percentual, l.valor, c.codigo AS categoria
            FROM condominio.lancamento_financeiro l
            LEFT JOIN condominio.categoria_financeira c ON c.id = l.categoria_id
            WHERE l.demonstrativo_id = :demonstrativo_id ORDER BY l.ordem
        """),
        engine, params={"demonstrativo_id": demonstrativo_id},
    )


def get_saldos_contas(demonstrativo_id: int) -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(
        text("""
            SELECT c.nome AS conta, s.saldo_anterior, s.creditos, s.debitos, s.saldo_final, s.inclui_transferencias
            FROM condominio.saldo_conta_mensal s
            JOIN condominio.conta_condominio c ON c.id = s.conta_id
            WHERE s.demonstrativo_id = :demonstrativo_id ORDER BY s.id
        """),
        engine, params={"demonstrativo_id": demonstrativo_id},
    )


def get_evolucao_despesas(condominio_id: int) -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(text("""
        WITH demonstrativos AS (
            SELECT id, competencia,
                   COUNT(*) OVER (PARTITION BY competencia) AS versoes,
                   ROW_NUMBER() OVER (PARTITION BY competencia ORDER BY id DESC) AS posicao
            FROM condominio.demonstrativo_mensal WHERE condominio_id = :condominio_id
        )
        SELECT d.competencia, d.versoes,
               COALESCE(grupo.codigo, c.codigo, 'SEM CATEGORIA') AS categoria,
               COALESCE(c.codigo, l.descricao_original) AS detalhe,
               SUM(l.valor) AS valor
        FROM demonstrativos d
        JOIN condominio.lancamento_financeiro l ON l.demonstrativo_id = d.id
        LEFT JOIN condominio.categoria_financeira c ON c.id = l.categoria_id
        LEFT JOIN LATERAL (
            WITH RECURSIVE ancestrais AS (
                SELECT id, categoria_pai_id, codigo, nivel
                FROM condominio.categoria_financeira WHERE id = l.categoria_id
                UNION ALL
                SELECT pai.id, pai.categoria_pai_id, pai.codigo, pai.nivel
                FROM condominio.categoria_financeira pai
                JOIN ancestrais filho ON filho.categoria_pai_id = pai.id
            )
            SELECT codigo FROM ancestrais WHERE nivel = 2 LIMIT 1
        ) grupo ON TRUE
        WHERE d.posicao = 1 AND l.natureza = 'DESPESA' AND NOT l.eh_totalizador
        GROUP BY d.competencia, d.versoes, grupo.codigo, c.codigo, l.descricao_original
        ORDER BY d.competencia, categoria, detalhe
    """), engine, params={"condominio_id": condominio_id})


def get_inadimplencia(demonstrativo_id: int) -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(
        text("""
            SELECT competencia, boletos_abertos, unidades_inadimplentes, percentual_unidades,
                   valor_sem_acrescimos, regularizados_periodo, novos_periodo
            FROM condominio.inadimplencia_mensal WHERE demonstrativo_id = :demonstrativo_id
        """),
        engine, params={"demonstrativo_id": demonstrativo_id},
    )
