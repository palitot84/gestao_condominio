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

    # o Neon (pooler/pgbouncer) rejeita "options=-c search_path=..." no startup;
    # define o search_path via SET logo após cada nova conexão física
    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("SET search_path TO condominio, public")
        cursor.close()

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
                INSERT INTO condominio (
                    nome, razao_social, cnpj, logradouro, numero, complemento,
                    bairro, cidade, uf, cep, email, telefone
                ) VALUES (
                    :nome, :razao_social, :cnpj, :logradouro, :numero, :complemento,
                    :bairro, :cidade, :uf, :cep, :email, :telefone
                )
            """),
            {
                "nome": nome, "razao_social": razao_social or None, "cnpj": cnpj,
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
        FROM condominio ORDER BY nome
    """), engine)


def update_condominio(
    condominio_id: int, nome: str, razao_social: str, cnpj: str, logradouro: str, numero: str,
    complemento: str, bairro: str, cidade: str, uf: str, cep: str, email: str, telefone: str, ativo: bool,
) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE condominio
                SET nome = :nome, razao_social = :razao_social, cnpj = :cnpj, logradouro = :logradouro,
                    numero = :numero, complemento = :complemento, bairro = :bairro, cidade = :cidade,
                    uf = :uf, cep = :cep, email = :email, telefone = :telefone, ativo = :ativo
                WHERE id = :condominio_id
            """),
            {
                "nome": nome, "razao_social": razao_social or None, "cnpj": cnpj,
                "logradouro": logradouro or None, "numero": numero or None, "complemento": complemento or None,
                "bairro": bairro or None, "cidade": cidade or None, "uf": uf or None, "cep": cep or None,
                "email": email or None, "telefone": telefone or None, "ativo": ativo, "condominio_id": condominio_id,
            },
        )


def delete_condominio(condominio_id: int) -> None:
    """Remove o condomínio. Falha se houver boletos vinculados (restrição do banco)."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM condominio WHERE id = :condominio_id"), {"condominio_id": condominio_id})


# ---------- Unidades ----------

def add_unidade(condominio_id: int, codigo: str, bloco: str, andar: str, fracao_ideal: float | None, observacoes: str) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO unidade (condominio_id, codigo, bloco, andar, fracao_ideal, observacoes)
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
        FROM unidade u
        JOIN condominio c ON c.id = u.condominio_id
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
                UPDATE unidade
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
        conn.execute(text("DELETE FROM unidade WHERE id = :unidade_id"), {"unidade_id": unidade_id})


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
                INSERT INTO boleto (
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
                INSERT INTO boleto_item (boleto_id, ordem, categoria_original, descricao, valor, parcela_atual, total_parcelas)
                VALUES (:boleto_id, :ordem, :categoria_original, :descricao, :valor, :parcela_atual, :total_parcelas)
            """),
            {
                "boleto_id": boleto_id, "ordem": ordem, "categoria_original": categoria_original or None,
                "descricao": descricao, "valor": valor, "parcela_atual": parcela_atual, "total_parcelas": total_parcelas,
            },
        )


def get_boletos(condominio_id: int | None = None) -> pd.DataFrame:
    engine = get_engine()
    query = "SELECT * FROM vw_boleto_resumo"
    params = {}
    if condominio_id is not None:
        query += " WHERE condominio = (SELECT nome FROM condominio WHERE id = :condominio_id)"
        params["condominio_id"] = condominio_id
    query += " ORDER BY competencia DESC, data_vencimento DESC"
    return pd.read_sql(text(query), engine, params=params)


def get_boleto_itens(boleto_id: int) -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql(
        text("""
            SELECT ordem, categoria_original, descricao, valor, parcela_atual, total_parcelas
            FROM boleto_item WHERE boleto_id = :boleto_id ORDER BY ordem
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
                INSERT INTO demonstrativo_mensal (
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
                INSERT INTO lancamento_financeiro (demonstrativo_id, ordem, descricao_original, natureza, valor)
                VALUES (:demonstrativo_id, :ordem, :descricao_original, :natureza, :valor)
            """),
            {
                "demonstrativo_id": demonstrativo_id, "ordem": ordem,
                "descricao_original": descricao_original, "natureza": natureza, "valor": valor,
            },
        )


def get_demonstrativos(condominio_id: int | None = None) -> pd.DataFrame:
    engine = get_engine()
    query = "SELECT * FROM vw_demonstrativo_resumo"
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
            SELECT ordem, descricao_original, natureza, valor
            FROM lancamento_financeiro WHERE demonstrativo_id = :demonstrativo_id ORDER BY ordem
        """),
        engine, params={"demonstrativo_id": demonstrativo_id},
    )
