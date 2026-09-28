"""Aplicativo Streamlit para gestão de condomínios, unidades, boletos e demonstrativos mensais."""
from datetime import date

import pandas as pd
import streamlit as st

import database as db

st.set_page_config(page_title="Boletos de Condomínios", page_icon="🏢", layout="centered")

MENU_OPTIONS = [
    "Gestão de Condomínios",
    "Gestão de Unidades",
    "Novo Boleto",
    "Novo Demonstrativo Mensal",
    "Boletos Cadastrados",
    "Demonstrativos Cadastrados",
]

st.sidebar.title("🏢 Menu")
choice = st.sidebar.radio("Navegação", MENU_OPTIONS)

MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


def mes_ano_competencia(key_prefix: str) -> date:
    col_mes, col_ano = st.columns(2)
    mes = col_mes.selectbox("Mês de referência", MESES, index=date.today().month - 1, key=f"{key_prefix}_mes")
    ano = col_ano.number_input(
        "Ano de referência", min_value=2000, max_value=2100, value=date.today().year, step=1, key=f"{key_prefix}_ano"
    )
    return date(int(ano), MESES.index(mes) + 1, 1)


def extras_editor(key: str) -> dict:
    st.caption("Campos adicionais (opcional) — use para informações que variam entre registros.")
    editado = st.data_editor(
        pd.DataFrame({"Campo": [], "Valor": []}), num_rows="dynamic", width="stretch", key=key,
    )
    return {
        str(row["Campo"]).strip(): row["Valor"]
        for _, row in editado.iterrows()
        if str(row["Campo"]).strip()
    }


def select_condominio(key: str) -> int | None:
    condominios = db.get_condominios()
    if condominios.empty:
        st.warning("Nenhum condomínio cadastrado ainda. Cadastre um condomínio primeiro.")
        return None
    options = dict(zip(condominios["nome"], condominios["id"]))
    chosen_name = st.selectbox("Condomínio", options.keys(), key=key)
    return options[chosen_name]


def select_unidade(condominio_id: int, key: str) -> int | None:
    unidades = db.get_unidades(condominio_id)
    if unidades.empty:
        st.warning("Este condomínio ainda não possui unidades cadastradas. Cadastre uma unidade primeiro.")
        return None
    labels = {f"{row.codigo} (bloco {row.bloco or '-'})": row.id for row in unidades.itertuples()}
    chosen_label = st.selectbox("Unidade", labels.keys(), key=key)
    return labels[chosen_label]


def endereco_fields(defaults: dict | None = None) -> dict:
    defaults = defaults or {}
    col1, col2 = st.columns([3, 1])
    logradouro = col1.text_input("Logradouro", value=defaults.get("logradouro") or "")
    numero = col2.text_input("Número", value=defaults.get("numero") or "")
    complemento = st.text_input("Complemento", value=defaults.get("complemento") or "")
    col3, col4, col5 = st.columns([2, 1, 1])
    bairro = col3.text_input("Bairro", value=defaults.get("bairro") or "")
    cidade = col4.text_input("Cidade", value=defaults.get("cidade") or "")
    uf = col5.text_input("UF", value=defaults.get("uf") or "", max_chars=2)
    cep = st.text_input("CEP (somente números)", value=defaults.get("cep") or "")
    return {
        "logradouro": logradouro, "numero": numero, "complemento": complemento,
        "bairro": bairro, "cidade": cidade, "uf": uf.upper(), "cep": db.only_digits(cep),
    }


if choice == "Gestão de Condomínios":
    st.header("🏢 Gestão de Condomínios")
    tab_criar, tab_alterar, tab_remover = st.tabs(["Criar", "Alterar", "Remover"])

    with tab_criar:
        with st.form("form_condominio_criar"):
            nome = st.text_input("Nome do condomínio")
            razao_social = st.text_input("Razão social")
            cnpj = st.text_input("CNPJ (somente números, 14 dígitos)")
            endereco = endereco_fields()
            col_email, col_tel = st.columns(2)
            email = col_email.text_input("E-mail")
            telefone = col_tel.text_input("Telefone")
            submitted = st.form_submit_button("Cadastrar")
            if submitted:
                cnpj_digits = db.only_digits(cnpj)
                if not nome.strip():
                    st.error("Informe o nome do condomínio.")
                elif len(cnpj_digits) != 14:
                    st.error("Informe um CNPJ válido com 14 dígitos.")
                else:
                    try:
                        db.add_condominio(
                            nome.strip(), razao_social.strip(), cnpj_digits, **endereco,
                            email=email.strip(), telefone=telefone.strip(),
                        )
                        st.success(f"Condomínio '{nome}' cadastrado com sucesso!")
                    except Exception as exc:
                        st.error(f"Erro ao cadastrar condomínio: {exc}")

    with tab_alterar:
        condominios = db.get_condominios()
        if condominios.empty:
            st.warning("Nenhum condomínio cadastrado ainda.")
        else:
            options = dict(zip(condominios["nome"], condominios["id"]))
            chosen_name = st.selectbox("Condomínio", options.keys(), key="alterar_condominio_select")
            condominio_id = options[chosen_name]
            row = condominios.loc[condominios["id"] == condominio_id].iloc[0]
            with st.form("form_condominio_alterar"):
                new_nome = st.text_input("Nome do condomínio", value=row["nome"])
                new_razao_social = st.text_input("Razão social", value=row["razao_social"] or "")
                new_cnpj = st.text_input("CNPJ (somente números, 14 dígitos)", value=row["cnpj"] or "")
                new_endereco = endereco_fields(row.to_dict())
                col_email, col_tel = st.columns(2)
                new_email = col_email.text_input("E-mail", value=row["email"] or "")
                new_telefone = col_tel.text_input("Telefone", value=row["telefone"] or "")
                new_ativo = st.checkbox("Ativo", value=bool(row["ativo"]))
                submitted = st.form_submit_button("Salvar alterações")
                if submitted:
                    cnpj_digits = db.only_digits(new_cnpj)
                    if not new_nome.strip():
                        st.error("Informe o nome do condomínio.")
                    elif len(cnpj_digits) != 14:
                        st.error("Informe um CNPJ válido com 14 dígitos.")
                    else:
                        try:
                            db.update_condominio(
                                condominio_id, new_nome.strip(), new_razao_social.strip(), cnpj_digits,
                                **new_endereco, email=new_email.strip(), telefone=new_telefone.strip(), ativo=new_ativo,
                            )
                            st.success("Condomínio atualizado com sucesso!")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Erro ao atualizar condomínio: {exc}")

    with tab_remover:
        condominios = db.get_condominios()
        if condominios.empty:
            st.warning("Nenhum condomínio cadastrado ainda.")
        else:
            options = dict(zip(condominios["nome"], condominios["id"]))
            chosen_name = st.selectbox("Condomínio", options.keys(), key="remover_condominio_select")
            condominio_id = options[chosen_name]
            st.warning("Só é possível remover um condomínio que não possua unidades ou boletos vinculados.")
            confirm = st.checkbox("Confirmo a remoção deste condomínio", key="confirm_remover_condominio")
            if st.button("Remover condomínio", disabled=not confirm):
                try:
                    db.delete_condominio(condominio_id)
                    st.success(f"Condomínio '{chosen_name}' removido com sucesso!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Erro ao remover condomínio: {exc}")

    st.subheader("Condomínios cadastrados")
    st.dataframe(db.get_condominios(), width="stretch", hide_index=True)

elif choice == "Gestão de Unidades":
    st.header("🏠 Gestão de Unidades")
    tab_criar, tab_alterar, tab_remover = st.tabs(["Criar", "Alterar", "Remover"])

    with tab_criar:
        condominio_id = select_condominio("criar_unidade_condominio_select")
        if condominio_id is not None:
            with st.form("form_unidade_criar"):
                col_codigo, col_bloco, col_andar = st.columns(3)
                codigo = col_codigo.text_input("Código/número da unidade")
                bloco = col_bloco.text_input("Bloco")
                andar = col_andar.text_input("Andar")
                fracao_ideal = st.number_input("Fração ideal (opcional)", min_value=0.0, step=0.0001, format="%.8f")
                observacoes = st.text_area("Observações")
                submitted = st.form_submit_button("Cadastrar")
                if submitted:
                    if not codigo.strip():
                        st.error("Informe o código da unidade.")
                    else:
                        try:
                            db.add_unidade(
                                condominio_id, codigo.strip(), bloco.strip(), andar.strip(),
                                fracao_ideal or None, observacoes.strip(),
                            )
                            st.success(f"Unidade '{codigo}' cadastrada com sucesso!")
                        except Exception as exc:
                            st.error(f"Erro ao cadastrar unidade: {exc}")

    with tab_alterar:
        unidades = db.get_unidades()
        if unidades.empty:
            st.warning("Nenhuma unidade cadastrada ainda.")
        else:
            labels = {f"{row.codigo} (bloco {row.bloco or '-'}) — {row.condominio_nome}": row.id for row in unidades.itertuples()}
            chosen_label = st.selectbox("Unidade", labels.keys(), key="alterar_unidade_select")
            unidade = unidades.loc[unidades["id"] == labels[chosen_label]].iloc[0]
            with st.form("form_unidade_alterar"):
                col_codigo, col_bloco, col_andar = st.columns(3)
                new_codigo = col_codigo.text_input("Código/número da unidade", value=unidade["codigo"])
                new_bloco = col_bloco.text_input("Bloco", value=unidade["bloco"] or "")
                new_andar = col_andar.text_input("Andar", value=unidade["andar"] or "")
                new_fracao = st.number_input(
                    "Fração ideal (opcional)", min_value=0.0, step=0.0001, format="%.8f",
                    value=float(unidade["fracao_ideal"] or 0),
                )
                new_observacoes = st.text_area("Observações", value=unidade["observacoes"] or "")
                submitted = st.form_submit_button("Salvar alterações")
                if submitted:
                    if not new_codigo.strip():
                        st.error("Informe o código da unidade.")
                    else:
                        try:
                            db.update_unidade(
                                int(unidade["id"]), new_codigo.strip(), new_bloco.strip(),
                                new_andar.strip(), new_fracao or None, new_observacoes.strip(),
                            )
                            st.success("Unidade atualizada com sucesso!")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Erro ao atualizar unidade: {exc}")

    with tab_remover:
        unidades = db.get_unidades()
        if unidades.empty:
            st.warning("Nenhuma unidade cadastrada ainda.")
        else:
            labels = {f"{row.codigo} (bloco {row.bloco or '-'}) — {row.condominio_nome}": row.id for row in unidades.itertuples()}
            chosen_label = st.selectbox("Unidade", labels.keys(), key="remover_unidade_select")
            unidade_id = labels[chosen_label]
            st.warning("Só é possível remover uma unidade que não possua boletos vinculados.")
            confirm = st.checkbox("Confirmo a remoção desta unidade", key="confirm_remover_unidade")
            if st.button("Remover unidade", disabled=not confirm):
                try:
                    db.delete_unidade(unidade_id)
                    st.success(f"Unidade '{chosen_label}' removida com sucesso!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Erro ao remover unidade: {exc}")

    st.subheader("Unidades cadastradas")
    st.dataframe(db.get_unidades(), width="stretch", hide_index=True)

elif choice == "Novo Boleto":
    st.header("🧾 Novo Boleto")
    condominio_id = select_condominio("novo_boleto_condominio_select")
    if condominio_id is not None:
        unidade_id = select_unidade(condominio_id, "novo_boleto_unidade_select")
        if unidade_id is not None:
            competencia = mes_ano_competencia("novo_boleto")

            col_doc, col_nosso = st.columns(2)
            numero_documento = col_doc.text_input("Número do documento")
            nosso_numero = col_nosso.text_input("Nosso número")

            col_linha, col_barras = st.columns(2)
            linha_digitavel = col_linha.text_input("Linha digitável (47-48 dígitos)")
            codigo_barras = col_barras.text_input("Código de barras (44 dígitos)")

            col_moeda, col_status = st.columns(2)
            moeda = col_moeda.text_input("Moeda", value="BRL", max_chars=3)
            status = col_status.selectbox("Status", db.STATUS_BOLETO)

            col_valor, col_desconto, col_pago = st.columns(3)
            valor_original = col_valor.number_input("Valor original", min_value=0.0, step=1.0, format="%.2f")
            valor_desconto = col_desconto.number_input("Valor de desconto", min_value=0.0, step=1.0, format="%.2f")
            valor_pago = col_pago.number_input("Valor pago (opcional)", min_value=0.0, step=1.0, format="%.2f")

            col_emissao, col_processamento = st.columns(2)
            data_emissao = col_emissao.date_input("Data de emissão", value=None)
            data_processamento = col_processamento.date_input("Data de processamento", value=None)

            col_venc, col_desc_limite, col_pag = st.columns(3)
            data_vencimento = col_venc.date_input("Data de vencimento *", value=date.today())
            data_limite_desconto = col_desc_limite.date_input("Data limite do desconto", value=None)
            data_pagamento = col_pag.date_input("Data de pagamento", value=None)

            instrucoes = st.text_area("Instruções")

            st.caption("Itens do boleto (opcional) — detalhamento de cada valor cobrado.")
            itens_editor = st.data_editor(
                pd.DataFrame({"Categoria": [], "Descrição": [], "Valor": []}),
                num_rows="dynamic", width="stretch", key="boleto_itens_editor",
            )

            dados_extras = extras_editor("boleto_extras_editor")

            if st.button("Salvar boleto"):
                if valor_desconto > valor_original:
                    st.error("O valor de desconto não pode ser maior que o valor original.")
                else:
                    try:
                        boleto_id = db.add_boleto(
                            condominio_id, unidade_id, competencia,
                            data_emissao, data_processamento, data_vencimento, data_pagamento,
                            numero_documento.strip(), nosso_numero.strip(),
                            db.only_digits(linha_digitavel) or None, db.only_digits(codigo_barras) or None,
                            moeda.strip().upper() or "BRL", valor_original, valor_desconto, data_limite_desconto,
                            valor_pago or None, status, instrucoes.strip(), dados_extras,
                        )
                        for ordem, (_, item) in enumerate(itens_editor.iterrows(), start=1):
                            descricao = str(item["Descrição"]).strip()
                            if descricao and descricao.lower() != "nan":
                                db.add_boleto_item(
                                    boleto_id, ordem, str(item["Categoria"]).strip() or None,
                                    descricao, float(item["Valor"] or 0), None, None,
                                )
                        st.success("Boleto salvo com sucesso!")
                    except Exception as exc:
                        st.error(f"Erro ao salvar boleto: {exc}")

elif choice == "Novo Demonstrativo Mensal":
    st.header("📊 Novo Demonstrativo Mensal")
    condominio_id = select_condominio("novo_demonstrativo_condominio_select")
    if condominio_id is not None:
        competencia = mes_ano_competencia("novo_demonstrativo")

        col_inicio, col_fim = st.columns(2)
        periodo_inicio = col_inicio.date_input("Início do período (opcional)", value=None)
        periodo_fim = col_fim.date_input("Fim do período (opcional)", value=None)

        col_saldo_ini, col_receitas, col_despesas = st.columns(3)
        saldo_inicial = col_saldo_ini.number_input("Saldo inicial", step=1.0, format="%.2f")
        total_receitas = col_receitas.number_input("Total de receitas", min_value=0.0, step=1.0, format="%.2f")
        total_despesas = col_despesas.number_input("Total de despesas", min_value=0.0, step=1.0, format="%.2f")

        col_mov, col_saldo_fim = st.columns(2)
        movimento_liquido = col_mov.number_input("Movimento líquido", step=1.0, format="%.2f")
        saldo_final = col_saldo_fim.number_input("Saldo final", step=1.0, format="%.2f")

        st.caption("Lançamentos financeiros (opcional) — detalhamento de receitas e despesas do mês.")
        lancamentos_editor = st.data_editor(
            pd.DataFrame({"Descrição": [], "Natureza": [], "Valor": []}),
            num_rows="dynamic", width="stretch", key="lancamentos_editor",
            column_config={"Natureza": st.column_config.SelectboxColumn(options=["RECEITA", "DESPESA"])},
        )

        dados_extras = extras_editor("demonstrativo_extras_editor")

        if st.button("Salvar demonstrativo"):
            try:
                demonstrativo_id = db.add_demonstrativo(
                    condominio_id, competencia, periodo_inicio, periodo_fim,
                    saldo_inicial or None, total_receitas or None, total_despesas or None,
                    movimento_liquido or None, saldo_final or None, dados_extras,
                )
                for ordem, (_, lanc) in enumerate(lancamentos_editor.iterrows(), start=1):
                    descricao = str(lanc["Descrição"]).strip()
                    natureza = str(lanc["Natureza"]).strip().upper()
                    if descricao and descricao.lower() != "nan" and natureza in ("RECEITA", "DESPESA"):
                        db.add_lancamento(demonstrativo_id, ordem, descricao, natureza, float(lanc["Valor"] or 0))
                st.success("Demonstrativo mensal salvo com sucesso!")
            except Exception as exc:
                st.error(f"Erro ao salvar demonstrativo: {exc}")

elif choice == "Boletos Cadastrados":
    st.header("📋 Boletos Cadastrados")
    condominios = db.get_condominios()
    if condominios.empty:
        st.warning("Nenhum condomínio cadastrado ainda.")
    else:
        filter_option = st.selectbox("Filtrar por condomínio (opcional)", ["Todos"] + condominios["nome"].tolist())
        if filter_option == "Todos":
            st.dataframe(db.get_boletos(), width="stretch", hide_index=True)
        else:
            condominio_id = int(condominios.loc[condominios["nome"] == filter_option, "id"].iloc[0])
            st.dataframe(db.get_boletos(condominio_id), width="stretch", hide_index=True)

elif choice == "Demonstrativos Cadastrados":
    st.header("📈 Demonstrativos Cadastrados")
    condominios = db.get_condominios()
    if condominios.empty:
        st.warning("Nenhum condomínio cadastrado ainda.")
    else:
        filter_option = st.selectbox("Filtrar por condomínio (opcional)", ["Todos"] + condominios["nome"].tolist())
        if filter_option == "Todos":
            st.dataframe(db.get_demonstrativos(), width="stretch", hide_index=True)
        else:
            condominio_id = int(condominios.loc[condominios["nome"] == filter_option, "id"].iloc[0])
            st.dataframe(db.get_demonstrativos(condominio_id), width="stretch", hide_index=True)

