"""Testes isolados: python -m unittest -v test_operacoes.

Simula Streamlit, pandas, SQLAlchemy e conexoes, sem credenciais ou banco real.
Nao valida execucao SQL, constraints PostgreSQL ou interacoes de interface.
"""

import ast
import importlib.util
import json
import os
import re
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch, sentinel


ROOT = Path(__file__).resolve().parent


class TestOperacoes(unittest.TestCase):
    def setUp(self):
        self.streamlit = ModuleType("streamlit")
        self.streamlit.cache_resource = lambda function: function
        self.streamlit.secrets = {
            "database_url": "postgresql://usuario:senha@localhost/banco_simulado",
        }
        self.pandas = ModuleType("pandas")
        self.pandas.DataFrame = object
        self.pandas.read_sql = MagicMock(return_value=sentinel.dataframe)
        self.sqlalchemy = ModuleType("sqlalchemy")
        self.sqlalchemy.text = MagicMock(side_effect=lambda statement: statement)
        self.sqlalchemy.create_engine = MagicMock(return_value=sentinel.engine)
        self.sqlalchemy.event = MagicMock()
        self.sqlalchemy.event.listens_for.return_value = lambda function: function
        engine_module = ModuleType("sqlalchemy.engine")
        engine_module.Engine = object

        spec = importlib.util.spec_from_file_location(
            "database_simulado", ROOT / "database.py",
        )
        self.db = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            "streamlit": self.streamlit,
            "pandas": self.pandas,
            "sqlalchemy": self.sqlalchemy,
            "sqlalchemy.engine": engine_module,
        }):
            spec.loader.exec_module(self.db)

        self.engine = MagicMock()
        self.transaction = self.engine.begin.return_value
        self.connection = self.transaction.__enter__.return_value
        self.transaction.__exit__.return_value = False
        self.connection.execute.return_value.scalar_one.return_value = 101
        engine_patch = patch.object(self.db, "get_engine", return_value=self.engine)
        self.real_get_engine = self.db.get_engine
        engine_patch.start()
        self.addCleanup(engine_patch.stop)

        self.condominio = {
            "nome": "Condominio Teste", "razao_social": "Empresa Teste",
            "cnpj": "12345678000190", "logradouro": "Rua Teste", "numero": "10",
            "complemento": "Predio", "bairro": "Centro", "cidade": "Rio de Janeiro",
            "uf": "RJ", "cep": "20000000", "email": "teste@example.com",
            "telefone": "21999999999",
        }
        self.unidade = {
            "codigo": "101", "bloco": "A", "andar": "1",
            "fracao_ideal": 0.01234567, "observacoes": "Unidade teste",
        }
        self.boleto = {
            "condominio_id": 1, "unidade_id": 2, "competencia": date(2026, 10, 1),
            "data_emissao": date(2026, 10, 2), "data_processamento": date(2026, 10, 3),
            "data_vencimento": date(2026, 10, 10), "data_pagamento": date(2026, 10, 9),
            "numero_documento": "DOC-1", "nosso_numero": "123",
            "linha_digitavel": "1" * 47, "codigo_barras": "2" * 44, "moeda": "BRL",
            "valor_original": 500.0, "valor_desconto": 20.0,
            "data_limite_desconto": date(2026, 10, 9), "valor_pago": 480.0,
            "status": "PAGO", "instrucoes": "Pagamento ate o vencimento",
            "dados_extras": {"referencia": "teste", "medicoes": [10, 20]},
        }
        self.demonstrativo = {
            "condominio_id": 1, "competencia": date(2026, 10, 1),
            "periodo_inicio": date(2026, 10, 1), "periodo_fim": date(2026, 10, 31),
            "saldo_inicial": -100.0, "total_receitas": 500.0, "total_despesas": 200.0,
            "movimento_liquido": 300.0, "saldo_final": 200.0,
            "dados_extras": {"conta": "principal"},
        }

    def assert_write(self, operation, table, expected):
        self.engine.begin.assert_called_once_with()
        self.transaction.__enter__.assert_called_once_with()
        self.connection.execute.assert_called_once()
        statement, params = self.connection.execute.call_args.args
        self.assertIn(f"{operation} condominio.{table}", statement)
        self.assertEqual(params, expected)
        placeholders = set(re.findall(r"(?<!:):([a-z_]+)", statement))
        self.assertEqual(placeholders, set(expected))
        self.transaction.__exit__.assert_called_once_with(None, None, None)

    def test_configuracao_engine(self):
        callbacks = []

        def register(engine, event_name):
            self.assertIs(engine, sentinel.engine)
            self.assertEqual(event_name, "begin")

            def capture(function):
                callbacks.append(function)
                return function

            return capture

        self.sqlalchemy.event.listens_for.side_effect = register
        for url, expected in [
            ("postgresql://localhost/teste", "postgresql+psycopg2://localhost/teste"),
            ("postgresql+psycopg2://localhost/teste", "postgresql+psycopg2://localhost/teste"),
        ]:
            with self.subTest(url=url):
                self.sqlalchemy.create_engine.reset_mock()
                self.streamlit.secrets["database_url"] = url
                self.assertIs(self.real_get_engine(), sentinel.engine)
                self.sqlalchemy.create_engine.assert_called_once_with(
                    expected, pool_pre_ping=True,
                )
                connection = MagicMock()
                callbacks[-1](connection)
                connection.exec_driver_sql.assert_called_once_with(
                    "SET LOCAL search_path TO condominio, public",
                )

    def test_only_digits(self):
        for value, expected in [
            ("12.345.678/0001-90", "12345678000190"),
            ("(21) 99999-9999", "21999999999"), ("", ""), ("abc", ""),
        ]:
            with self.subTest(value=value):
                self.assertEqual(self.db.only_digits(value), expected)

    def test_criar_condominio(self):
        self.assertIsNone(self.db.add_condominio(**self.condominio))
        self.assert_write("INSERT INTO", "condominio", self.condominio)

    def test_alterar_condominio(self):
        expected = dict(self.condominio, condominio_id=1, ativo=False)
        self.assertIsNone(self.db.update_condominio(**expected))
        self.assert_write("UPDATE", "condominio", expected)

    def test_condominio_campos_opcionais(self):
        optional = set(self.condominio) - {"nome", "logradouro", "numero"}
        values = {key: "" if key in optional else value for key, value in self.condominio.items()}
        for operation in ("add_condominio", "update_condominio"):
            with self.subTest(operation=operation):
                self.engine.reset_mock()
                args = dict(values)
                if operation == "update_condominio":
                    args.update(condominio_id=1, ativo=True)
                getattr(self.db, operation)(**args)
                expected = {key: None if key in optional else value for key, value in args.items()}
                self.assert_write(
                    "INSERT INTO" if operation == "add_condominio" else "UPDATE",
                    "condominio", expected,
                )

    def test_criar_unidade(self):
        expected = dict(self.unidade, condominio_id=1)
        self.assertIsNone(self.db.add_unidade(**expected))
        self.assert_write("INSERT INTO", "unidade", expected)

    def test_alterar_unidade(self):
        expected = dict(self.unidade, unidade_id=2)
        self.assertIsNone(self.db.update_unidade(**expected))
        self.assert_write("UPDATE", "unidade", expected)

    def test_unidade_campos_opcionais(self):
        for operation, identifier in [("add_unidade", "condominio_id"), ("update_unidade", "unidade_id")]:
            with self.subTest(operation=operation):
                self.engine.reset_mock()
                args = {identifier: 1, "codigo": "101", "bloco": "", "andar": "",
                        "fracao_ideal": None, "observacoes": ""}
                getattr(self.db, operation)(**args)
                expected = dict(args, andar=None, observacoes=None)
                self.assert_write(
                    "INSERT INTO" if operation == "add_unidade" else "UPDATE", "unidade", expected,
                )

    def test_excluir_registros(self):
        for operation, table, identifier in [
            ("delete_condominio", "condominio", "condominio_id"),
            ("delete_unidade", "unidade", "unidade_id"),
        ]:
            with self.subTest(operation=operation):
                self.engine.reset_mock()
                self.assertIsNone(getattr(self.db, operation)(42))
                self.assert_write("DELETE FROM", table, {identifier: 42})

    def test_criar_boleto_todos_status(self):
        self.assertEqual(self.db.STATUS_BOLETO, ["PENDENTE", "PAGO", "VENCIDO", "CANCELADO", "RENEGOCIADO"])
        for status in self.db.STATUS_BOLETO:
            with self.subTest(status=status):
                self.engine.reset_mock()
                values = dict(self.boleto, status=status)
                self.assertEqual(self.db.add_boleto(**values), 101)
                expected = dict(values, dados_extras=json.dumps(values["dados_extras"]))
                self.assert_write("INSERT INTO", "boleto", expected)
                statement = self.connection.execute.call_args.args[0]
                self.assertIn("CAST(:dados_extras AS JSONB)", statement)
                self.assertIn("RETURNING id", statement)
                self.connection.execute.return_value.scalar_one.assert_called_once_with()

    def test_boleto_campos_opcionais(self):
        values = dict(self.boleto)
        empty_text = ["numero_documento", "nosso_numero", "linha_digitavel", "codigo_barras", "instrucoes"]
        for field in empty_text:
            values[field] = ""
        for field in ["data_emissao", "data_processamento", "data_pagamento", "data_limite_desconto", "valor_pago"]:
            values[field] = None
        values.update(moeda="", dados_extras={}, valor_desconto=0)
        self.assertEqual(self.db.add_boleto(**values), 101)
        expected = dict(values, moeda="BRL", dados_extras="{}")
        for field in empty_text:
            expected[field] = None
        self.assert_write("INSERT INTO", "boleto", expected)

    def test_criar_item_boleto(self):
        for category, current, total in [("Taxa", 1, 12), ("", None, None)]:
            with self.subTest(category=category):
                self.engine.reset_mock()
                values = {"boleto_id": 101, "ordem": 1, "categoria_original": category,
                          "descricao": "Taxa mensal", "valor": 500.0,
                          "parcela_atual": current, "total_parcelas": total}
                self.assertIsNone(self.db.add_boleto_item(**values))
                self.assert_write("INSERT INTO", "boleto_item", dict(values, categoria_original=category or None))

    def test_criar_demonstrativo(self):
        for optional in (False, True):
            with self.subTest(optional=optional):
                self.engine.reset_mock()
                values = dict(self.demonstrativo)
                if optional:
                    for field in set(values) - {"condominio_id", "competencia", "dados_extras"}:
                        values[field] = None
                    values["dados_extras"] = {}
                self.assertEqual(self.db.add_demonstrativo(**values), 101)
                self.assert_write("INSERT INTO", "demonstrativo_mensal", dict(values, dados_extras=json.dumps(values["dados_extras"])))
                statement = self.connection.execute.call_args.args[0]
                self.assertIn("CAST(:dados_extras AS JSONB)", statement)
                self.assertIn("RETURNING id", statement)
                self.connection.execute.return_value.scalar_one.assert_called_once_with()

    def test_criar_lancamento(self):
        for nature in ("RECEITA", "DESPESA"):
            with self.subTest(nature=nature):
                self.engine.reset_mock()
                values = {"demonstrativo_id": 101, "ordem": 1,
                          "descricao_original": "Movimento teste", "natureza": nature, "valor": 200.0}
                self.assertIsNone(self.db.add_lancamento(**values))
                self.assert_write("INSERT INTO", "lancamento_financeiro", values)

    def test_consultar_condominios(self):
        self.assertIs(self.db.get_condominios(), sentinel.dataframe)
        statement, engine = self.pandas.read_sql.call_args.args
        self.assertIs(engine, self.engine)
        self.assertIn("FROM condominio.condominio ORDER BY nome", statement)
        self.pandas.read_sql.assert_called_once()

    def test_consultas_com_e_sem_filtro(self):
        for function, table, filter_sql, ordering in [
            ("get_unidades", "unidade", "WHERE u.condominio_id = :condominio_id", "ORDER BY c.nome, u.bloco, u.codigo"),
            ("get_boletos", "vw_boleto_resumo", "WHERE condominio = (SELECT nome FROM condominio.condominio WHERE id = :condominio_id)", "ORDER BY competencia DESC, data_vencimento DESC"),
            ("get_demonstrativos", "vw_demonstrativo_resumo", "WHERE condominio_id = :condominio_id", "ORDER BY competencia DESC"),
        ]:
            for condominio_id in (None, 42):
                with self.subTest(function=function, condominio_id=condominio_id):
                    self.pandas.read_sql.reset_mock()
                    result = getattr(self.db, function)(condominio_id)
                    self.assertIs(result, sentinel.dataframe)
                    self.pandas.read_sql.assert_called_once()
                    statement, engine = self.pandas.read_sql.call_args.args
                    self.assertIs(engine, self.engine)
                    self.assertIn(f"FROM condominio.{table}", statement)
                    self.assertIn(ordering, statement)
                    if condominio_id is None:
                        self.assertNotIn("WHERE", statement)
                    else:
                        self.assertIn(filter_sql, statement)
                    self.assertEqual(self.pandas.read_sql.call_args.kwargs["params"],
                                     {} if condominio_id is None else {"condominio_id": 42})

    def test_evolucao_despesas_exclui_totais_e_versoes_repetidas(self):
        self.assertIs(self.db.get_evolucao_despesas(42), sentinel.dataframe)
        self.pandas.read_sql.assert_called_once()
        statement, engine = self.pandas.read_sql.call_args.args
        self.assertIs(engine, self.engine)
        self.assertIn("NOT l.eh_totalizador", statement)
        self.assertIn("l.natureza = 'DESPESA'", statement)
        self.assertIn("d.posicao = 1", statement)
        self.assertIn("WITH RECURSIVE ancestrais", statement)
        self.assertEqual(self.pandas.read_sql.call_args.kwargs["params"], {"condominio_id": 42})

    def test_consultar_detalhes(self):
        for function, table, identifier in [
            ("get_boleto_itens", "boleto_item", "boleto_id"),
            ("get_boleto_encargos", "boleto_encargo", "boleto_id"),
            ("get_lancamentos", "lancamento_financeiro", "demonstrativo_id"),
            ("get_saldos_contas", "saldo_conta_mensal", "demonstrativo_id"),
            ("get_inadimplencia", "inadimplencia_mensal", "demonstrativo_id"),
        ]:
            with self.subTest(function=function):
                self.pandas.read_sql.reset_mock()
                self.assertIs(getattr(self.db, function)(101), sentinel.dataframe)
                self.pandas.read_sql.assert_called_once()
                statement, engine = self.pandas.read_sql.call_args.args
                self.assertIs(engine, self.engine)
                self.assertIn(f"FROM condominio.{table}", statement)
                self.assertIn(f"{identifier} = :{identifier}", statement)
                self.assertEqual(self.pandas.read_sql.call_args.kwargs["params"], {identifier: 101})

    def test_erro_exclusao_propagado_para_rollback(self):
        for function in ("delete_condominio", "delete_unidade"):
            with self.subTest(function=function):
                self.engine.reset_mock()
                error = RuntimeError("Simulacao de registro com vinculos")
                self.connection.execute.side_effect = error
                with self.assertRaises(RuntimeError) as context:
                    getattr(self.db, function)(1)
                self.assertIs(context.exception, error)
                self.assertIs(self.transaction.__exit__.call_args.args[1], error)

    def test_erro_tabela_ausente_propagado(self):
        error = RuntimeError("Simulacao de UndefinedTable")
        self.pandas.read_sql.side_effect = error
        with self.assertRaises(RuntimeError) as context:
            self.db.get_condominios()
        self.assertIs(context.exception, error)

    def test_validacao_cadastro_condominio(self):
        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
        validator = next(node for node in tree.body
                         if isinstance(node, ast.FunctionDef) and node.name == "validar_condominio")
        namespace = {"db": self.db}
        exec(compile(ast.Module(body=[validator], type_ignores=[]), "app.py", "exec"), namespace)
        for name, street, number, cnpj, valid in [
            ("Teste", "Rua Teste", "10", "", True),
            ("Teste", "Rua Teste", "10", "  ", True),
            ("Teste", "Rua Teste", "10", "12.345.678/0001-90", True),
            ("  ", "Rua Teste", "10", "", False),
            ("Teste", "  ", "10", "", False),
            ("Teste", "Rua Teste", "  ", "", False),
            ("Teste", "Rua Teste", "10", "123", False),
            ("Teste", "Rua Teste", "10", "abc", False),
        ]:
            with self.subTest(name=name, street=street, number=number, cnpj=cnpj):
                error = namespace["validar_condominio"](name, {"logradouro": street, "numero": number}, cnpj)
                if valid:
                    self.assertIsNone(error)
                else:
                    self.assertIsInstance(error, str)
                    self.assertTrue(error)


    def test_leitura_completa_cristiano(self):
        from boleto_pdf import ler_boleto_pdf

        path = ROOT / "boleto_1048921-Set2026.PDF"
        parsed = ler_boleto_pdf(path.read_bytes(), path.name)
        boleto, demonstrativo = parsed["boleto"], parsed["demonstrativo"]
        self.assertEqual(parsed["unidade"], "1301")
        self.assertEqual(parsed["condominio"]["cnpj"], "05295147000170")
        self.assertEqual((parsed["pagador"]["nome"], parsed["pagador"]["cpf_mascarado"]),
                         ("ALVARO ALENCAR BARBOSA PALITOT", "***44788***"))
        self.assertEqual(boleto["competencia"], date(2026, 9, 1))
        self.assertEqual((boleto["data_emissao"], boleto["data_processamento"], boleto["data_vencimento"]),
                         (date(2026, 8, 24), date(2026, 9, 25), date(2026, 9, 5)))
        self.assertEqual(boleto["valor_original"], Decimal("2286.90"))
        self.assertEqual(boleto["valor_desconto"], Decimal("103.95"))
        self.assertEqual(boleto["nosso_numero"], "001/00574171620-3")
        self.assertEqual(boleto["linha_digitavel"], "48190000030000515057841716200146115600000228690")
        self.assertEqual(boleto["codigo_barras"], "48191156000002286900000000005150574171620014")
        self.assertIsNone(boleto["data_pagamento"])
        self.assertIsNone(boleto["valor_pago"])
        extras = boleto["dados_extras"]
        self.assertEqual(extras["banco"]["codigo"], "481-2")
        self.assertEqual(extras["valor_com_desconto"], "2182.95")
        self.assertEqual(extras["correspondencia"]["remetente"]["email"], "atendimento@controlarcondominios.com.br")
        self.assertEqual([item["valor"] for item in parsed["itens"]], [Decimal("2172.55"), Decimal("114.35")])
        self.assertEqual([(e["tipo"], e["percentual"], e["valor_fixo"]) for e in parsed["encargos"]], [
            ("MULTA", Decimal("2.00"), Decimal("45.74")), ("JUROS", Decimal("0.033"), Decimal("0.75")),
            ("DESCONTO", None, Decimal("103.95"))])
        self.assertEqual(demonstrativo["competencia"], date(2026, 7, 1))
        self.assertEqual([demonstrativo[campo] for campo in ("saldo_inicial", "total_receitas", "total_despesas",
                                                              "movimento_liquido", "saldo_final")],
                         [Decimal("529792.56"), Decimal("105730.25"), Decimal("102017.37"),
                          Decimal("3712.88"), Decimal("533505.44")])
        lancamentos = demonstrativo["lancamentos"]
        self.assertEqual(len(lancamentos), 89)
        self.assertEqual(sum(not l["eh_totalizador"] for l in lancamentos), 71)
        self.assertEqual([l["ordem"] for l in lancamentos], list(range(1, 90)))
        self.assertEqual(lancamentos[1]["valor"], Decimal("-4573.80"))
        self.assertEqual(lancamentos[84]["dados_extras"]["pagina"], 2)
        academia = [l["categoria_codigo"] for l in lancamentos if l["descricao_original"] == "MANUT. ACADEMIA"]
        self.assertEqual(len(set(academia)), 2)
        self.assertEqual([c["nome"] for c in demonstrativo["contas"]], [
            "CONTA PRINCIPAL", "INVESTIMENTO FIC GIRO", "INVESTIMENTO RENDA FIXA", "CONTA CORRENTE - SICREDI",
            "SICRED INVEST", "CAPITAL SOCIAL", "INVESTIMENTO FIC GIRO II"])
        self.assertEqual(sum(c["saldo_final"] for c in demonstrativo["contas"]), Decimal("533505.44"))
        inadimplencia = demonstrativo["inadimplencia"]
        self.assertEqual((inadimplencia["boletos_abertos"], inadimplencia["unidades_inadimplentes"],
                          inadimplencia["valor_sem_acrescimos"]), (2, 1, Decimal("4864.31")))
        self.assertEqual(len(parsed["paginas"]), 2)
        self.assertEqual(parsed["documento"]["tamanho_bytes"], len(path.read_bytes()))

    def test_leitura_completa_rosa_do_prado(self):
        from boleto_pdf import ler_boleto_pdf

        path = ROOT / "boleto_110228-Set2026.PDF"
        parsed = ler_boleto_pdf(path.read_bytes(), path.name)
        boleto, demonstrativo = parsed["boleto"], parsed["demonstrativo"]
        self.assertEqual(parsed["unidade"], "207")
        self.assertEqual(boleto["competencia"], date(2026, 8, 1))
        self.assertEqual(boleto["data_vencimento"], date(2026, 9, 10))
        self.assertEqual(boleto["valor_original"], Decimal("667.50"))
        self.assertEqual(boleto["valor_desconto"], Decimal("0.00"))
        self.assertEqual(boleto["dados_extras"]["contato_instrucoes"],
                         {"email": "execon.assessoria@yahoo.com.br", "telefone": "(83) 30239076"})
        self.assertEqual(len(parsed["itens"]), 4)
        self.assertEqual((parsed["itens"][1]["parcela_atual"], parsed["itens"][1]["total_parcelas"]), (2, 4))
        self.assertEqual([e["tipo"] for e in parsed["encargos"]], ["MULTA", "JUROS"])
        self.assertEqual([demonstrativo[campo] for campo in ("saldo_inicial", "total_receitas", "total_despesas",
                                                              "movimento_liquido", "saldo_final")],
                         [Decimal("41011.38"), Decimal("27487.53"), Decimal("18586.77"),
                          Decimal("8900.76"), Decimal("49912.14")])
        lancamentos = demonstrativo["lancamentos"]
        self.assertEqual(len(lancamentos), 50)
        self.assertEqual(lancamentos[0]["percentual"], Decimal("64.97"))
        encargos_sociais = lancamentos[22]
        self.assertEqual(encargos_sociais["descricao_original"], "Total de Encargos Sociais - Impostos - Taxas")
        self.assertEqual(len(encargos_sociais["dados_extras"]["linhas_pdf"]), 2)
        odonto = next(l for l in lancamentos if l["descricao_original"].startswith("Odonto"))
        self.assertEqual(odonto["categoria_codigo"], "DESPESAS > PESSOAL > CONVÊNIO ODONTOLÓGICO > ODONTO - FUNCIONÁRIO")
        self.assertEqual([c["nome"] for c in demonstrativo["contas"]], [
            "Conta Principal", "Conta poupança - Fundo de Caixa 13º / Férias",
            "Conta poupança Fundo de Reserva / Taxa Extra", "Conta Investimento 2"])
        self.assertIsNone(demonstrativo["inadimplencia"])

    def ler_com_texto_alterado(self, nome, original, alterado):
        from pypdf import PdfReader

        from boleto_pdf import ler_boleto_pdf

        paginas = [page.extract_text() for page in PdfReader(ROOT / nome).pages]
        self.assertTrue(any(original in texto for texto in paginas), original)
        reader = MagicMock(is_encrypted=False, metadata={})
        reader.pages = [MagicMock(extract_text=MagicMock(return_value=texto.replace(original, alterado, 1)))
                        for texto in paginas]
        with patch("boleto_pdf.PdfReader", return_value=reader):
            return ler_boleto_pdf((ROOT / nome).read_bytes(), nome)

    def test_validacoes_rejeitam_pdf_inconsistente(self):
        cristiano = "boleto_1048921-Set2026.PDF"
        for original, alterado in [
            ("2.172,55", "2.172,56"),
            ("97.764,75", "97.764,76"),
            ("105.730,25", "105.730,26"),
            ("SALDO EM 31/07/2026                                      533.505,44",
             "SALDO EM 31/07/2026                                      533.505,45"),
            ("4.316,15", "4.316,16"),
            ("=          2", "=          3"),
            ("41716.200146", "41716.200147"),
            ("05/09/2026\n2.286,90", "06/09/2026\n2.286,90"),
            ("RESUMO FINANCEIRO", "TEXTO INESPERADO\nRESUMO FINANCEIRO"),
        ]:
            with self.subTest(original=original):
                with self.assertRaises(ValueError):
                    self.ler_com_texto_alterado(cristiano, original, alterado)
        with self.assertRaises(ValueError):
            self.ler_com_texto_alterado("boleto_110228-Set2026.PDF", "\naxas", "\noutra")

    def test_pdf_invalido(self):
        from boleto_pdf import ler_boleto_pdf

        with self.assertRaises(ValueError):
            ler_boleto_pdf(b"arquivo invalido", "invalido.pdf")

    def test_pdf_rejeita_vinculo_incorreto(self):
        from boleto_pdf import ler_boleto_pdf

        path = ROOT / "boleto_1048921-Set2026.PDF"
        parsed = ler_boleto_pdf(path.read_bytes(), path.name)
        for link in (None, {"codigo": "207", "cnpj": "05295147000170"},
                     {"codigo": "1301", "cnpj": "00575608000144"}):
            with self.subTest(link=link):
                self.engine.reset_mock()
                self.connection.execute.return_value.mappings.return_value.one_or_none.return_value = link
                with self.assertRaises(ValueError):
                    self.db.salvar_boleto_pdf(1, 2, parsed)
                self.assertEqual(self.connection.execute.call_count, 1)
                self.assertIsNotNone(self.transaction.__exit__.call_args.args[1])

    def test_salvar_pdf_usa_uma_transacao(self):
        with patch.object(self.db, "importar_boleto_pdf", return_value={"boleto_id": 7}) as importar:
            self.assertEqual(self.db.salvar_boleto_pdf(1, 2, sentinel.leitura), {"boleto_id": 7})
        importar.assert_called_once_with(self.connection, 1, 2, sentinel.leitura)
        self.transaction.__exit__.assert_called_once_with(None, None, None)
        error = RuntimeError("Falha simulada na gravacao")
        with patch.object(self.db, "importar_boleto_pdf", side_effect=error):
            with self.assertRaises(RuntimeError):
                self.db.salvar_boleto_pdf(1, 2, sentinel.leitura)
        self.assertIs(self.transaction.__exit__.call_args.args[1], error)


def conferir_importacao(test, conn, leitura, resultado, condominio_id, unidade_id):
    """Compara campo a campo o que está no banco com o que foi lido do PDF."""
    from sqlalchemy import text

    def consultar(sql, **params):
        return conn.execute(text(sql), params).mappings()

    documento = consultar("SELECT * FROM condominio.documento_importado WHERE id = :id",
                          id=resultado["documento_id"]).one()
    for campo in ("nome_arquivo", "mime_type", "tamanho_bytes", "hash_sha256", "local_armazenamento",
                  "motor_extracao", "versao_motor", "metadados"):
        test.assertEqual(documento[campo], leitura["documento"][campo], f"documento_importado.{campo}")
    test.assertEqual((documento["status"], documento["condominio_id"]), ("PROCESSADO", condominio_id))
    paginas = consultar("""
        SELECT numero_pagina, texto_extraido, dados_brutos FROM condominio.extracao_pagina
        WHERE documento_id = :id ORDER BY numero_pagina
    """, id=resultado["documento_id"]).all()
    test.assertEqual([dict(p) for p in paginas], leitura["paginas"])

    pessoa = consultar("SELECT nome, cpf_mascarado FROM condominio.pessoa WHERE id = :id",
                       id=resultado["pagador_id"]).one()
    test.assertEqual(dict(pessoa), {"nome": leitura["pagador"]["nome"],
                                    "cpf_mascarado": leitura["pagador"]["cpf_mascarado"]})
    test.assertEqual(consultar("""
        SELECT COUNT(*) AS total FROM condominio.unidade_pessoa
        WHERE unidade_id = :unidade_id AND pessoa_id = :pessoa_id AND papel = 'PAGADOR' AND fim_vigencia IS NULL
    """, unidade_id=unidade_id, pessoa_id=resultado["pagador_id"]).one()["total"], 1)

    boleto = consultar("SELECT * FROM condominio.boleto WHERE id = :id", id=resultado["boleto_id"]).one()
    test.assertEqual((boleto["condominio_id"], boleto["unidade_id"], boleto["documento_id"], boleto["pagador_id"]),
                     (condominio_id, unidade_id, resultado["documento_id"], resultado["pagador_id"]))
    for campo, esperado in leitura["boleto"].items():
        if campo == "dados_extras" or (campo in ("status", "data_pagamento", "valor_pago")
                                       and not resultado["boleto_novo"]):
            continue
        test.assertEqual(boleto[campo], esperado, f"boleto.{campo}")
    test.assertEqual(boleto["dados_extras"], dict(leitura["boleto"]["dados_extras"],
                                                  documento_id=resultado["documento_id"],
                                                  demonstrativo_id=resultado["demonstrativo_id"]))
    itens = consultar("""
        SELECT ordem, categoria_original, descricao, valor, parcela_atual, total_parcelas, dados_extras
        FROM condominio.boleto_item WHERE boleto_id = :id ORDER BY ordem
    """, id=resultado["boleto_id"]).all()
    test.assertEqual([dict(item) for item in itens], leitura["itens"])
    encargos = consultar("""
        SELECT tipo, percentual, valor_fixo, periodicidade, inicio_aplicacao, descricao
        FROM condominio.boleto_encargo WHERE boleto_id = :id ORDER BY id
    """, id=resultado["boleto_id"]).all()
    test.assertEqual([dict(e) for e in encargos], leitura["encargos"])

    esperado = leitura["demonstrativo"]
    demonstrativo = consultar("SELECT * FROM condominio.demonstrativo_mensal WHERE id = :id",
                              id=resultado["demonstrativo_id"]).one()
    test.assertEqual(demonstrativo["condominio_id"], condominio_id)
    for campo in ("competencia", "periodo_inicio", "periodo_fim", "saldo_inicial", "total_receitas",
                  "total_despesas", "movimento_liquido", "saldo_final"):
        test.assertEqual(demonstrativo[campo], esperado[campo], f"demonstrativo_mensal.{campo}")
    extras = dict(demonstrativo["dados_extras"])
    test.assertIn(leitura["documento"]["hash_sha256"], extras.pop("documentos_origem"))
    test.assertEqual(extras, esperado["dados_extras"])
    lancamentos = consultar("""
        SELECT l.ordem, l.descricao_original, l.natureza::text AS natureza, l.valor, l.percentual,
               l.eh_totalizador, l.nivel_original, c.codigo AS categoria_codigo, l.dados_extras
        FROM condominio.lancamento_financeiro l
        JOIN condominio.categoria_financeira c ON c.id = l.categoria_id
        WHERE l.demonstrativo_id = :id ORDER BY l.ordem
    """, id=resultado["demonstrativo_id"]).all()
    test.assertEqual([dict(l) for l in lancamentos], esperado["lancamentos"])
    categorias = consultar("""
        SELECT c.natureza::text AS natureza, c.codigo, c.nivel, c.nome_normalizado, p.codigo AS pai_codigo
        FROM condominio.categoria_financeira c
        LEFT JOIN condominio.categoria_financeira p ON p.id = c.categoria_pai_id
        WHERE c.condominio_id = :condominio_id AND c.codigo = ANY(:codigos)
        ORDER BY c.nivel, c.codigo, c.natureza
    """, condominio_id=condominio_id, codigos=[c["codigo"] for c in esperado["categorias"]]).all()
    test.assertEqual(sorted((dict(c) for c in categorias), key=lambda c: (c["nivel"], c["codigo"], c["natureza"])),
                     sorted(esperado["categorias"], key=lambda c: (c["nivel"], c["codigo"], c["natureza"])))
    contas = consultar("""
        SELECT c.nome, s.saldo_anterior, s.creditos, s.debitos, s.saldo_final, s.inclui_transferencias
        FROM condominio.saldo_conta_mensal s JOIN condominio.conta_condominio c ON c.id = s.conta_id
        WHERE s.demonstrativo_id = :id ORDER BY s.id
    """, id=resultado["demonstrativo_id"]).all()
    test.assertEqual([dict(c) for c in contas], esperado["contas"])
    inadimplencia = consultar("""
        SELECT competencia, boletos_abertos, unidades_inadimplentes, percentual_unidades, valor_sem_acrescimos,
               regularizados_periodo, novos_periodo, dados_extras
        FROM condominio.inadimplencia_mensal WHERE demonstrativo_id = :id
    """, id=resultado["demonstrativo_id"]).one_or_none()
    test.assertEqual(dict(inadimplencia) if inadimplencia else None, esperado["inadimplencia"])

    # Conferência independente do leitor: somas recalculadas pelo próprio banco.
    totais = consultar("""
        SELECT d.total_receitas, d.total_despesas, d.movimento_liquido, d.saldo_inicial, d.saldo_final,
               SUM(l.valor) FILTER (WHERE NOT l.eh_totalizador AND l.natureza = 'RECEITA') AS folhas_receita,
               SUM(l.valor) FILTER (WHERE NOT l.eh_totalizador AND l.natureza = 'DESPESA') AS folhas_despesa,
               (SELECT SUM(saldo_anterior) FROM condominio.saldo_conta_mensal WHERE demonstrativo_id = d.id) AS contas_inicial,
               (SELECT SUM(saldo_final) FROM condominio.saldo_conta_mensal WHERE demonstrativo_id = d.id) AS contas_final
        FROM condominio.demonstrativo_mensal d
        JOIN condominio.lancamento_financeiro l ON l.demonstrativo_id = d.id
        WHERE d.id = :id GROUP BY d.id
    """, id=resultado["demonstrativo_id"]).one()
    test.assertEqual(totais["folhas_receita"], totais["total_receitas"])
    test.assertEqual(totais["folhas_despesa"], totais["total_despesas"])
    test.assertEqual(totais["total_receitas"] - totais["total_despesas"], totais["movimento_liquido"])
    test.assertEqual(totais["saldo_inicial"] + totais["movimento_liquido"], totais["saldo_final"])
    if esperado["contas"]:
        test.assertEqual((totais["contas_inicial"], totais["contas_final"]),
                         (totais["saldo_inicial"], totais["saldo_final"]))
    test.assertEqual(consultar("SELECT SUM(valor) AS total FROM condominio.boleto_item WHERE boleto_id = :id",
                               id=resultado["boleto_id"]).one()["total"], boleto["valor_original"])
    duplicidades = consultar("""
        SELECT (SELECT COUNT(*) FROM condominio.documento_importado WHERE hash_sha256 = :hash) AS documentos,
               (SELECT COUNT(*) FROM condominio.boleto
                 WHERE condominio_id = :condominio_id AND numero_documento = :numero) AS boletos,
               (SELECT COUNT(*) FROM condominio.demonstrativo_mensal
                 WHERE condominio_id = :condominio_id AND competencia = :competencia) AS demonstrativos
    """, hash=leitura["documento"]["hash_sha256"], condominio_id=condominio_id,
        numero=leitura["boleto"]["numero_documento"], competencia=esperado["competencia"]).one()
    test.assertEqual(dict(duplicidades), {"documentos": 1, "boletos": 1, "demonstrativos": 1})
    resumo = consultar("SELECT unidade, pagador FROM condominio.vw_boleto_resumo WHERE id = :id",
                       id=resultado["boleto_id"]).one()
    test.assertEqual(dict(resumo), {"unidade": leitura["unidade"], "pagador": leitura["pagador"]["nome"]})


@unittest.skipUnless(os.environ.get("TESTAR_BANCO_PDF") == "1",
                     "Integracao real desativada: definir TESTAR_BANCO_PDF=1")
class TestIntegracaoPDF(unittest.TestCase):
    ARQUIVOS = [("boleto_1048921-Set2026.PDF", "05295147000170"), ("boleto_110228-Set2026.PDF", "00575608000144")]

    def vinculo(self, conn, leitura, cnpj):
        from sqlalchemy import text

        condominio_id = conn.execute(text("SELECT id FROM condominio.condominio WHERE cnpj = :cnpj"),
                                     {"cnpj": cnpj}).scalar_one()
        unidade_id = conn.execute(text("""
            SELECT id FROM condominio.unidade WHERE condominio_id = :condominio_id AND codigo = :codigo AND bloco = ''
        """), {"condominio_id": condominio_id, "codigo": leitura["unidade"]}).scalar_one()
        return condominio_id, unidade_id

    def test_importacao_completa_em_transacao_revertida(self):
        import database as db
        from boleto_pdf import ler_boleto_pdf

        with db.get_engine().connect() as conn:
            transacao = conn.begin()
            try:
                for nome, cnpj in self.ARQUIVOS:
                    with self.subTest(arquivo=nome):
                        leitura = ler_boleto_pdf((ROOT / nome).read_bytes(), nome)
                        condominio_id, unidade_id = self.vinculo(conn, leitura, cnpj)
                        primeiro = db.importar_boleto_pdf(conn, condominio_id, unidade_id, leitura)
                        segundo = db.importar_boleto_pdf(conn, condominio_id, unidade_id, leitura)
                        self.assertEqual(primeiro, dict(segundo, boleto_novo=primeiro["boleto_novo"]))
                        conferir_importacao(self, conn, leitura, segundo, condominio_id, unidade_id)
            finally:
                transacao.rollback()

    @unittest.skipUnless(os.environ.get("GRAVAR_BANCO_PDF") == "1",
                         "Gravacao definitiva desativada: definir GRAVAR_BANCO_PDF=1")
    def test_gravar_e_conferir_os_dois_boletos(self):
        import database as db
        from boleto_pdf import ler_boleto_pdf

        engine = db.get_engine()
        for nome, cnpj in self.ARQUIVOS:
            with self.subTest(arquivo=nome):
                leitura = ler_boleto_pdf((ROOT / nome).read_bytes(), nome)
                with engine.connect() as conn:
                    condominio_id, unidade_id = self.vinculo(conn, leitura, cnpj)
                resultado = db.salvar_boleto_pdf(condominio_id, unidade_id, leitura)
                with engine.connect() as conn:
                    conferir_importacao(self, conn, leitura, resultado, condominio_id, unidade_id)
                print(f"\nGravado e conferido: {nome} -> {resultado}")


if __name__ == "__main__":
    unittest.main(verbosity=2)