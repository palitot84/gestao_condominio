"""Aplicativo Streamlit para gestão de condomínios, unidades, boletos e demonstrativos mensais."""
from datetime import date

import pandas as pd
import streamlit as st

import database as db
from boleto_pdf import ler_boleto_pdf

st.set_page_config(page_title="Boletos de Condomínios", page_icon="🏢", layout="centered")

MENU_OPTIONS = [
    "Gestão de Condomínios",
    "Gestão de Unidades",
    "Importar Boleto e Demonstrativo",
    "Boletos Cadastrados",
    "Demonstrativos Cadastrados",
    "Análise de Gastos",
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
    logradouro = col1.text_input("Logradouro *", value=defaults.get("logradouro") or "")
    numero = col2.text_input("Número *", value=defaults.get("numero") or "")
    complemento = st.text_input("Complemento", value=defaults.get("complemento") or "")
    col3, col4, col5 = st.columns([2, 1, 1])
    bairro = col3.text_input("Bairro", value=defaults.get("bairro") or "")
    cidade = col4.text_input("Cidade", value=defaults.get("cidade") or "")
    uf = col5.text_input("UF", value=defaults.get("uf") or "", max_chars=2)
    cep = st.text_input("CEP (somente números)", value=defaults.get("cep") or "")
    return {
        "logradouro": logradouro.strip(), "numero": numero.strip(), "complemento": complemento,
        "bairro": bairro, "cidade": cidade, "uf": uf.upper(), "cep": db.only_digits(cep),
    }


def validar_condominio(nome: str, endereco: dict, cnpj: str) -> str | None:
    if not nome.strip():
        return "Informe o nome do condomínio."
    if not endereco["logradouro"].strip() or not endereco["numero"].strip():
        return "Informe o logradouro e o número do endereço."
    if cnpj.strip() and len(db.only_digits(cnpj)) != 14:
        return "Informe um CNPJ válido com 14 dígitos ou deixe o campo vazio."
    return None


def moeda(valor) -> str:
    return f"R$ {valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def tabela_lancamentos(lancamentos: pd.DataFrame) -> pd.DataFrame:
    tabela = lancamentos.copy()
    tabela["descricao"] = [("    " * int(nivel or 0)) + descricao
                           for nivel, descricao in zip(tabela["nivel_original"], tabela["descricao_original"])]
    tabela["tipo"] = tabela["eh_totalizador"].map({True: "Total", False: "Item"})
    return tabela[["ordem", "natureza", "tipo", "descricao", "percentual", "valor"]]


def analisar_gastos() -> None:
    st.header("Análise de Gastos")
    condominio_id = select_condominio("analise_condominio")
    if condominio_id is None:
        return
    dados = db.get_evolucao_despesas(condominio_id)
    if dados.empty:
        st.info("Nenhuma despesa registrada em demonstrativos deste condomínio.")
        return
    dados["competencia"] = pd.to_datetime(dados["competencia"])
    dados["valor"] = pd.to_numeric(dados["valor"])
    meses = sorted(dados["competencia"].unique())
    if len(meses) > 1:
        inicio, fim = st.select_slider(
            "Período", options=meses, value=(meses[0], meses[-1]),
            format_func=lambda mes: pd.Timestamp(mes).strftime("%m/%Y"),
        )
        dados = dados.loc[dados["competencia"].between(inicio, fim)].copy()
    else:
        st.caption(f"Competência disponível: {pd.Timestamp(meses[0]):%m/%Y}")
        st.info("Há apenas um mês registrado. A evolução aparecerá ao importar os próximos demonstrativos.")
    if dados["versoes"].gt(1).any():
        st.warning("Há competências com mais de um demonstrativo. A análise usa o registro de maior ID de cada mês.")
    nivel = st.radio("Agrupamento", ["Categoria principal", "Detalhamento"], horizontal=True)
    coluna = "categoria" if nivel == "Categoria principal" else "detalhe"
    categorias = sorted(dados[coluna].unique())
    selecionadas = st.multiselect("Categorias financeiras", categorias, default=categorias)
    if not selecionadas:
        st.info("Nenhuma categoria selecionada.")
        return
    recorte = dados.loc[dados[coluna].isin(selecionadas)].copy()
    observado = dados.groupby("competencia")["valor"].sum().index
    calendario = pd.date_range(observado.min(), observado.max(), freq="MS")
    mensal = recorte.pivot_table(index="competencia", columns=coluna, values="valor", aggfunc="sum")
    mensal = mensal.reindex(observado, columns=selecionadas).fillna(0).reindex(calendario)
    mensal.index.name = "Competência"
    total = mensal.sum(axis=1, min_count=1)
    registrados = total.dropna()
    anterior = total.shift(1)
    variacao = (total - anterior).div(anterior.where(anterior.ne(0))).mul(100)
    col_total, col_media, col_ultimo = st.columns(3)
    col_total.metric("Gastos no período", moeda(registrados.sum()))
    col_media.metric("Média por mês registrado", moeda(registrados.mean()))
    ultimo_mes = registrados.index[-1]
    percentual = variacao.loc[ultimo_mes]
    col_ultimo.metric(
        f"Último mês ({ultimo_mes:%m/%Y})", moeda(registrados.iloc[-1]),
        delta=f"{percentual:+.1f}%" if pd.notna(percentual) else None,
        delta_color="inverse",
    )
    st.subheader("Evolução mensal por categoria")
    st.line_chart(mensal, height=360)
    st.subheader("Composição mensal dos gastos")
    st.bar_chart(mensal, stack=True, height=360)
    st.subheader("Gastos mensais e variação")
    resumo = pd.DataFrame({"Gastos (R$)": total, "Variação mensal (%)": variacao})
    resumo.index = resumo.index.strftime("%m/%Y")
    st.dataframe(resumo, width="stretch")
    st.subheader("Valores por categoria")
    tabela = mensal.copy()
    tabela.index = tabela.index.strftime("%m/%Y")
    st.dataframe(tabela, width="stretch")
    st.caption("Somente despesas sem totalizadores. Meses sem demonstrativo ficam sem valor. "
               "Valores e indicadores consideram as categorias selecionadas.")
    st.download_button(
        "Exportar CSV", data=tabela.to_csv(sep=";", decimal=",").encode("utf-8-sig"),
        file_name=f"gastos_condominio_{condominio_id}.csv", mime="text/csv",
    )


def carregar_boleto_pdf(condominio_id: int, unidade_id: int) -> None:
    arquivo = st.file_uploader("PDF do boleto e demonstrativo mensal", type=["pdf"], key="boleto_pdf_upload")
    if arquivo is None:
        return
    try:
        leitura = ler_boleto_pdf(arquivo.getvalue(), arquivo.name)
    except Exception as exc:
        st.error(f"Erro ao ler PDF: {exc}")
        return
    boleto, demonstrativo = leitura["boleto"], leitura["demonstrativo"]
    extras = boleto["dados_extras"]
    st.success("PDF lido e conferido: somas, saldos, linha digitável e vencimento conferem.")
    st.write(f"**{leitura['condominio']['nome']}** — CNPJ {leitura['condominio']['cnpj']} — unidade {leitura['unidade']}")
    col_documento, col_competencia, col_vencimento, col_valor = st.columns(4)
    col_documento.metric("Documento", boleto["numero_documento"])
    col_competencia.metric("Competência", boleto["competencia"].strftime("%m/%Y"))
    col_vencimento.metric("Vencimento", boleto["data_vencimento"].strftime("%d/%m/%Y"))
    col_valor.metric("Valor", moeda(boleto["valor_original"]))

    aba_boleto, aba_demonstrativo, aba_contas, aba_texto = st.tabs(
        ["Cobrança", f"Receitas e despesas {demonstrativo['competencia']:%m/%Y}", "Contas e inadimplência", "Texto do PDF"])
    with aba_boleto:
        st.write(f"Pagador: {leitura['pagador']['nome']} ({leitura['pagador']['cpf_mascarado']})")
        st.write(f"Nosso número: {boleto['nosso_numero']} — Linha digitável: {boleto['linha_digitavel']}")
        st.write(f"Emissão {boleto['data_emissao']:%d/%m/%Y} — processamento {boleto['data_processamento']:%d/%m/%Y}"
                 f" — desconto {moeda(boleto['valor_desconto'])}")
        st.dataframe(pd.DataFrame(leitura["itens"]).drop(columns=["dados_extras"]), width="stretch", hide_index=True)
        st.dataframe(pd.DataFrame(leitura["encargos"]), width="stretch", hide_index=True)
        st.json({chave: valor for chave, valor in extras.items() if chave != "correspondencia"}, expanded=False)
        st.json(extras["correspondencia"], expanded=False)
    with aba_demonstrativo:
        col_inicial, col_receitas, col_despesas, col_final = st.columns(4)
        col_inicial.metric("Saldo inicial", moeda(demonstrativo["saldo_inicial"]))
        col_receitas.metric("Receitas", moeda(demonstrativo["total_receitas"]))
        col_despesas.metric("Despesas", moeda(demonstrativo["total_despesas"]))
        col_final.metric("Saldo final", moeda(demonstrativo["saldo_final"]))
        st.dataframe(tabela_lancamentos(pd.DataFrame(demonstrativo["lancamentos"])), width="stretch", hide_index=True)
    with aba_contas:
        st.dataframe(pd.DataFrame(demonstrativo["contas"]), width="stretch", hide_index=True)
        if demonstrativo["inadimplencia"]:
            st.json(demonstrativo["inadimplencia"]["dados_extras"], expanded=False)
        else:
            st.caption("O PDF não contém quadro de inadimplência.")
    with aba_texto:
        for pagina in leitura["paginas"]:
            st.text(pagina["texto_extraido"])

    confirmado = st.checkbox("Confirmo os dados e o vínculo com o condomínio e a unidade selecionados",
                             key=f"confirmar_pdf_{condominio_id}_{unidade_id}_{extras['sha256']}")
    if st.button("Salvar boleto e demonstrativo", disabled=not confirmado):
        try:
            resultado = db.salvar_boleto_pdf(condominio_id, unidade_id, leitura)
            situacao = "registrado" if resultado["boleto_novo"] else "atualizado (reimportação, sem duplicar)"
            st.success(
                f"Boleto {resultado['boleto_id']} {situacao}: {resultado['itens']} itens, "
                f"{resultado['encargos']} encargos, demonstrativo {resultado['demonstrativo_id']} com "
                f"{resultado['lancamentos']} lançamentos, {resultado['contas']} contas"
                f"{' e inadimplência' if resultado['inadimplencia'] else ''}.")
            if resultado["demonstrativo_reaproveitado"]:
                st.info("O demonstrativo desta competência já existia com o mesmo conteúdo e foi reaproveitado.")
        except Exception as exc:
            st.error(f"Erro ao salvar PDF: {exc}")


if choice == "Gestão de Condomínios":
    st.header("🏢 Gestão de Condomínios")
    tab_criar, tab_alterar, tab_remover = st.tabs(["Criar", "Alterar", "Remover"])

    with tab_criar:
        with st.form("form_condominio_criar"):
            nome = st.text_input("Nome do condomínio *")
            razao_social = st.text_input("Razão social")
            cnpj = st.text_input("CNPJ (opcional, 14 dígitos)")
            endereco = endereco_fields()
            col_email, col_tel = st.columns(2)
            email = col_email.text_input("E-mail")
            telefone = col_tel.text_input("Telefone")
            submitted = st.form_submit_button("Cadastrar")
            if submitted:
                cnpj_digits = db.only_digits(cnpj)
                erro = validar_condominio(nome, endereco, cnpj)
                if erro:
                    st.error(erro)
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
                new_nome = st.text_input("Nome do condomínio *", value=row["nome"])
                new_razao_social = st.text_input("Razão social", value=row["razao_social"] or "")
                new_cnpj = st.text_input("CNPJ (opcional, 14 dígitos)", value=row["cnpj"] or "")
                new_endereco = endereco_fields(row.to_dict())
                col_email, col_tel = st.columns(2)
                new_email = col_email.text_input("E-mail", value=row["email"] or "")
                new_telefone = col_tel.text_input("Telefone", value=row["telefone"] or "")
                new_ativo = st.checkbox("Ativo", value=bool(row["ativo"]))
                submitted = st.form_submit_button("Salvar alterações")
                if submitted:
                    cnpj_digits = db.only_digits(new_cnpj)
                    erro = validar_condominio(new_nome, new_endereco, new_cnpj)
                    if erro:
                        st.error(erro)
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

elif choice == "Importar Boleto e Demonstrativo":
    st.header("Importar Boleto e Demonstrativo")
    condominio_id = select_condominio("novo_boleto_condominio_select")
    if condominio_id is not None:
        unidade_id = select_unidade(condominio_id, "novo_boleto_unidade_select")
        if unidade_id is not None:
            carregar_boleto_pdf(condominio_id, unidade_id)

elif choice == "Boletos Cadastrados":
    st.header("📋 Boletos Cadastrados")
    condominios = db.get_condominios()
    if condominios.empty:
        st.warning("Nenhum condomínio cadastrado ainda.")
    else:
        filter_option = st.selectbox("Filtrar por condomínio (opcional)", ["Todos"] + condominios["nome"].tolist())
        if filter_option == "Todos":
            boletos = db.get_boletos()
        else:
            condominio_id = int(condominios.loc[condominios["nome"] == filter_option, "id"].iloc[0])
            boletos = db.get_boletos(condominio_id)
        st.dataframe(boletos, width="stretch", hide_index=True)
        if not boletos.empty:
            opcoes = {f"{row.id} — {row.condominio} / {row.unidade} — {row.competencia:%m/%Y}": int(row.id)
                      for row in boletos.itertuples()}
            boleto_id = opcoes[st.selectbox("Detalhar boleto", opcoes.keys())]
            detalhe = db.get_boleto_detalhe(boleto_id)
            st.write(f"Pagador: {detalhe['pagador'] or '-'} — Arquivo: {detalhe['nome_arquivo'] or 'cadastro manual'}")
            st.subheader("Composição da cobrança")
            st.dataframe(db.get_boleto_itens(boleto_id), width="stretch", hide_index=True)
            st.subheader("Encargos e descontos")
            st.dataframe(db.get_boleto_encargos(boleto_id), width="stretch", hide_index=True)
            with st.expander("Todos os dados do boleto"):
                st.json({chave: str(valor) if chave != "dados_extras" else valor for chave, valor in detalhe.items()})
            demonstrativo_id = (detalhe["dados_extras"] or {}).get("demonstrativo_id")
            if demonstrativo_id:
                st.caption(f"Receitas e despesas que acompanham este boleto: demonstrativo {demonstrativo_id} "
                           "(menu Demonstrativos Cadastrados).")

elif choice == "Análise de Gastos":
    analisar_gastos()

elif choice == "Demonstrativos Cadastrados":
    st.header("📈 Demonstrativos Cadastrados")
    condominios = db.get_condominios()
    if condominios.empty:
        st.warning("Nenhum condomínio cadastrado ainda.")
    else:
        filter_option = st.selectbox("Filtrar por condomínio (opcional)", ["Todos"] + condominios["nome"].tolist())
        if filter_option == "Todos":
            demonstrativos = db.get_demonstrativos()
        else:
            condominio_id = int(condominios.loc[condominios["nome"] == filter_option, "id"].iloc[0])
            demonstrativos = db.get_demonstrativos(condominio_id)
        st.dataframe(demonstrativos, width="stretch", hide_index=True)
        if not demonstrativos.empty:
            opcoes = {f"{row.id} — {row.condominio} — {row.competencia:%m/%Y}": int(row.id)
                      for row in demonstrativos.itertuples()}
            demonstrativo_id = opcoes[st.selectbox("Detalhar demonstrativo", opcoes.keys())]
            st.subheader("Receitas e despesas")
            st.dataframe(tabela_lancamentos(db.get_lancamentos(demonstrativo_id)), width="stretch", hide_index=True)
            st.subheader("Saldos por conta")
            st.dataframe(db.get_saldos_contas(demonstrativo_id), width="stretch", hide_index=True)
            inadimplencia = db.get_inadimplencia(demonstrativo_id)
            if not inadimplencia.empty:
                st.subheader("Inadimplência")
                st.dataframe(inadimplencia, width="stretch", hide_index=True)

