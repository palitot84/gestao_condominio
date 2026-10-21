-- Estrutura usada pela aplicação (espelha o banco em uso, PostgreSQL 15+).
-- Pode ser executado em banco vazio ou existente: só cria o que falta, sem apagar dados.
BEGIN;

CREATE SCHEMA IF NOT EXISTS condominio;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'status_boleto' AND typnamespace = 'condominio'::regnamespace) THEN
        CREATE TYPE condominio.status_boleto AS ENUM ('PENDENTE', 'PAGO', 'VENCIDO', 'CANCELADO', 'RENEGOCIADO');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'status_extracao' AND typnamespace = 'condominio'::regnamespace) THEN
        CREATE TYPE condominio.status_extracao AS ENUM ('RECEBIDO', 'PROCESSANDO', 'PROCESSADO', 'REVISAR', 'ERRO');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'natureza_financeira' AND typnamespace = 'condominio'::regnamespace) THEN
        CREATE TYPE condominio.natureza_financeira AS ENUM ('RECEITA', 'DESPESA');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tipo_pessoa_unidade' AND typnamespace = 'condominio'::regnamespace) THEN
        CREATE TYPE condominio.tipo_pessoa_unidade AS ENUM ('PROPRIETARIO', 'MORADOR', 'PAGADOR', 'INQUILINO', 'OUTRO');
    END IF;
END $$;

CREATE OR REPLACE FUNCTION condominio.fn_atualizar_timestamp()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.atualizado_em := now();
    RETURN NEW;
END $$;

-- Tabela qualificada com o schema: não depende do search_path da sessão.
CREATE OR REPLACE FUNCTION condominio.fn_validar_boleto_unidade()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM condominio.unidade u
        WHERE u.id = NEW.unidade_id AND u.condominio_id = NEW.condominio_id
    ) THEN
        RAISE EXCEPTION 'A unidade % não pertence ao condomínio %', NEW.unidade_id, NEW.condominio_id;
    END IF;
    RETURN NEW;
END $$;

CREATE TABLE IF NOT EXISTS condominio.administradora (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nome TEXT NOT NULL,
    razao_social TEXT,
    cnpj VARCHAR(14),
    email TEXT,
    telefone TEXT,
    endereco JSONB NOT NULL DEFAULT '{}'::JSONB,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_administradora_cnpj CHECK (cnpj IS NULL OR cnpj ~ '^[0-9]{14}$'),
    CONSTRAINT uq_administradora_cnpj UNIQUE NULLS NOT DISTINCT (cnpj)
);

CREATE TABLE IF NOT EXISTS condominio.condominio (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    administradora_id BIGINT REFERENCES condominio.administradora (id) ON DELETE SET NULL,
    nome TEXT NOT NULL,
    razao_social TEXT,
    cnpj VARCHAR(14),
    logradouro TEXT NOT NULL,
    numero TEXT NOT NULL,
    complemento TEXT,
    bairro TEXT,
    cidade TEXT,
    uf CHAR(2),
    cep VARCHAR(8),
    email TEXT,
    telefone TEXT,
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_condominio_cnpj CHECK (cnpj ~ '^[0-9]{14}$'),
    CONSTRAINT ck_condominio_uf CHECK (uf IS NULL OR uf ~ '^[A-Z]{2}$'),
    CONSTRAINT ck_condominio_cep CHECK (cep IS NULL OR cep ~ '^[0-9]{8}$'),
    CONSTRAINT uq_condominio_cnpj UNIQUE (cnpj)
);

-- Regras do cadastro: CNPJ opcional; nome e endereço (logradouro e número) obrigatórios.
ALTER TABLE condominio.condominio ALTER COLUMN cnpj DROP NOT NULL;
ALTER TABLE condominio.condominio ALTER COLUMN logradouro SET NOT NULL;
ALTER TABLE condominio.condominio ALTER COLUMN numero SET NOT NULL;

CREATE TABLE IF NOT EXISTS condominio.unidade (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    condominio_id BIGINT NOT NULL REFERENCES condominio.condominio (id) ON DELETE CASCADE,
    codigo TEXT NOT NULL,
    bloco TEXT NOT NULL DEFAULT '',
    andar TEXT,
    fracao_ideal NUMERIC(12, 8),
    observacoes TEXT,
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_unidade_fracao CHECK (fracao_ideal IS NULL OR fracao_ideal >= 0),
    CONSTRAINT uq_unidade UNIQUE (condominio_id, codigo, bloco)
);

CREATE TABLE IF NOT EXISTS condominio.pessoa (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nome TEXT NOT NULL,
    cpf_hash VARCHAR(64),
    cpf_mascarado TEXT,
    email TEXT,
    telefone TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_pessoa_cpf_hash CHECK (cpf_hash IS NULL OR cpf_hash ~ '^[0-9a-fA-F]{64}$')
);

CREATE TABLE IF NOT EXISTS condominio.unidade_pessoa (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    unidade_id BIGINT NOT NULL REFERENCES condominio.unidade (id) ON DELETE CASCADE,
    pessoa_id BIGINT NOT NULL REFERENCES condominio.pessoa (id) ON DELETE CASCADE,
    papel condominio.tipo_pessoa_unidade NOT NULL,
    inicio_vigencia DATE NOT NULL DEFAULT CURRENT_DATE,
    fim_vigencia DATE,
    principal BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT ck_unidade_pessoa_periodo CHECK (fim_vigencia IS NULL OR fim_vigencia >= inicio_vigencia),
    CONSTRAINT uq_unidade_pessoa UNIQUE (unidade_id, pessoa_id, papel, inicio_vigencia)
);

CREATE TABLE IF NOT EXISTS condominio.documento_importado (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    condominio_id BIGINT REFERENCES condominio.condominio (id) ON DELETE SET NULL,
    nome_arquivo TEXT NOT NULL,
    mime_type TEXT NOT NULL DEFAULT 'application/pdf',
    tamanho_bytes BIGINT,
    hash_sha256 VARCHAR(64) NOT NULL,
    local_armazenamento TEXT NOT NULL,
    status condominio.status_extracao NOT NULL DEFAULT 'RECEBIDO',
    motor_extracao TEXT,
    versao_motor TEXT,
    metadados JSONB NOT NULL DEFAULT '{}'::JSONB,
    importado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    processado_em TIMESTAMPTZ,
    mensagem_erro TEXT,
    CONSTRAINT ck_documento_hash CHECK (hash_sha256 ~ '^[0-9a-fA-F]{64}$'),
    CONSTRAINT ck_documento_tamanho CHECK (tamanho_bytes IS NULL OR tamanho_bytes >= 0),
    CONSTRAINT uq_documento_hash UNIQUE (hash_sha256)
);

CREATE TABLE IF NOT EXISTS condominio.extracao_pagina (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    documento_id BIGINT NOT NULL REFERENCES condominio.documento_importado (id) ON DELETE CASCADE,
    numero_pagina INTEGER NOT NULL,
    texto_extraido TEXT,
    confianca_media NUMERIC(5, 4),
    dados_brutos JSONB NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT ck_extracao_pagina_numero CHECK (numero_pagina > 0),
    CONSTRAINT ck_extracao_confianca CHECK (confianca_media IS NULL OR (confianca_media >= 0 AND confianca_media <= 1)),
    CONSTRAINT uq_extracao_pagina UNIQUE (documento_id, numero_pagina)
);

CREATE TABLE IF NOT EXISTS condominio.boleto (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    documento_id BIGINT REFERENCES condominio.documento_importado (id) ON DELETE SET NULL,
    condominio_id BIGINT NOT NULL REFERENCES condominio.condominio (id) ON DELETE RESTRICT,
    unidade_id BIGINT NOT NULL REFERENCES condominio.unidade (id) ON DELETE RESTRICT,
    pagador_id BIGINT REFERENCES condominio.pessoa (id) ON DELETE SET NULL,
    competencia DATE NOT NULL,
    data_emissao DATE,
    data_processamento DATE,
    data_vencimento DATE NOT NULL,
    data_pagamento DATE,
    numero_documento TEXT,
    nosso_numero TEXT,
    linha_digitavel VARCHAR(48),
    codigo_barras VARCHAR(44),
    moeda CHAR(3) NOT NULL DEFAULT 'BRL',
    valor_original NUMERIC(14, 2) NOT NULL,
    valor_desconto NUMERIC(14, 2) NOT NULL DEFAULT 0,
    data_limite_desconto DATE,
    valor_pago NUMERIC(14, 2),
    status condominio.status_boleto NOT NULL DEFAULT 'PENDENTE',
    instrucoes TEXT,
    dados_extras JSONB NOT NULL DEFAULT '{}'::JSONB,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_boleto_competencia CHECK (date_trunc('month', competencia)::date = competencia),
    CONSTRAINT ck_boleto_linha CHECK (linha_digitavel IS NULL OR linha_digitavel ~ '^[0-9]{47,48}$'),
    CONSTRAINT ck_boleto_codigo CHECK (codigo_barras IS NULL OR codigo_barras ~ '^[0-9]{44}$'),
    CONSTRAINT ck_boleto_valores CHECK (valor_original >= 0 AND valor_desconto >= 0
        AND valor_desconto <= valor_original AND (valor_pago IS NULL OR valor_pago >= 0)),
    CONSTRAINT uq_boleto_documento UNIQUE NULLS NOT DISTINCT (condominio_id, numero_documento),
    CONSTRAINT uq_boleto_linha UNIQUE NULLS NOT DISTINCT (linha_digitavel)
);

CREATE TABLE IF NOT EXISTS condominio.boleto_item (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    boleto_id BIGINT NOT NULL REFERENCES condominio.boleto (id) ON DELETE CASCADE,
    ordem INTEGER NOT NULL DEFAULT 1,
    categoria_original TEXT,
    descricao TEXT NOT NULL,
    valor NUMERIC(14, 2) NOT NULL,
    parcela_atual INTEGER,
    total_parcelas INTEGER,
    dados_extras JSONB NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT ck_boleto_item_ordem CHECK (ordem > 0),
    CONSTRAINT ck_boleto_item_parcela CHECK (parcela_atual IS NULL
        OR (parcela_atual > 0 AND total_parcelas IS NOT NULL AND total_parcelas >= parcela_atual)),
    CONSTRAINT uq_boleto_item_ordem UNIQUE (boleto_id, ordem)
);

CREATE TABLE IF NOT EXISTS condominio.boleto_encargo (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    boleto_id BIGINT NOT NULL REFERENCES condominio.boleto (id) ON DELETE CASCADE,
    tipo TEXT NOT NULL,
    percentual NUMERIC(10, 6),
    valor_fixo NUMERIC(14, 2),
    periodicidade TEXT,
    inicio_aplicacao DATE,
    descricao TEXT,
    CONSTRAINT ck_encargo_tipo CHECK (tipo IN ('MULTA', 'JUROS', 'CORRECAO', 'DESCONTO', 'OUTRO')),
    CONSTRAINT ck_encargo_valores CHECK ((percentual IS NULL OR percentual >= 0) AND (valor_fixo IS NULL OR valor_fixo >= 0))
);

CREATE TABLE IF NOT EXISTS condominio.demonstrativo_mensal (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    documento_id BIGINT REFERENCES condominio.documento_importado (id) ON DELETE SET NULL,
    condominio_id BIGINT NOT NULL REFERENCES condominio.condominio (id) ON DELETE CASCADE,
    competencia DATE NOT NULL,
    periodo_inicio DATE,
    periodo_fim DATE,
    saldo_inicial NUMERIC(16, 2),
    total_receitas NUMERIC(16, 2),
    total_despesas NUMERIC(16, 2),
    movimento_liquido NUMERIC(16, 2),
    saldo_final NUMERIC(16, 2),
    dados_extras JSONB NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT ck_demonstrativo_competencia CHECK (date_trunc('month', competencia)::date = competencia),
    CONSTRAINT ck_demonstrativo_periodo CHECK (periodo_fim IS NULL OR periodo_inicio IS NULL OR periodo_fim >= periodo_inicio),
    CONSTRAINT uq_demonstrativo UNIQUE NULLS NOT DISTINCT (condominio_id, competencia, documento_id)
);

CREATE TABLE IF NOT EXISTS condominio.categoria_financeira (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    condominio_id BIGINT REFERENCES condominio.condominio (id) ON DELETE CASCADE,
    categoria_pai_id BIGINT REFERENCES condominio.categoria_financeira (id) ON DELETE RESTRICT,
    natureza condominio.natureza_financeira NOT NULL,
    codigo TEXT,
    nome_normalizado TEXT NOT NULL,
    nivel SMALLINT NOT NULL DEFAULT 1,
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT ck_categoria_nivel CHECK (nivel > 0),
    CONSTRAINT uq_categoria UNIQUE NULLS NOT DISTINCT (condominio_id, natureza, codigo, nome_normalizado)
);

CREATE TABLE IF NOT EXISTS condominio.lancamento_financeiro (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    demonstrativo_id BIGINT NOT NULL REFERENCES condominio.demonstrativo_mensal (id) ON DELETE CASCADE,
    categoria_id BIGINT REFERENCES condominio.categoria_financeira (id) ON DELETE SET NULL,
    ordem INTEGER NOT NULL,
    descricao_original TEXT NOT NULL,
    natureza condominio.natureza_financeira NOT NULL,
    valor NUMERIC(16, 2) NOT NULL,
    percentual NUMERIC(9, 6),
    eh_totalizador BOOLEAN NOT NULL DEFAULT FALSE,
    nivel_original SMALLINT,
    dados_extras JSONB NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT ck_lancamento_ordem CHECK (ordem > 0),
    CONSTRAINT ck_lancamento_percentual CHECK (percentual IS NULL OR (percentual >= -1000 AND percentual <= 1000)),
    CONSTRAINT uq_lancamento_ordem UNIQUE (demonstrativo_id, ordem)
);

CREATE TABLE IF NOT EXISTS condominio.conta_condominio (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    condominio_id BIGINT NOT NULL REFERENCES condominio.condominio (id) ON DELETE CASCADE,
    nome TEXT NOT NULL,
    tipo TEXT,
    instituicao TEXT,
    identificador_hash VARCHAR(64),
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_conta UNIQUE (condominio_id, nome, instituicao)
);

CREATE TABLE IF NOT EXISTS condominio.saldo_conta_mensal (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    demonstrativo_id BIGINT NOT NULL REFERENCES condominio.demonstrativo_mensal (id) ON DELETE CASCADE,
    conta_id BIGINT NOT NULL REFERENCES condominio.conta_condominio (id) ON DELETE CASCADE,
    saldo_anterior NUMERIC(16, 2) NOT NULL DEFAULT 0,
    creditos NUMERIC(16, 2) NOT NULL DEFAULT 0,
    debitos NUMERIC(16, 2) NOT NULL DEFAULT 0,
    saldo_final NUMERIC(16, 2) NOT NULL,
    inclui_transferencias BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_saldo_conta UNIQUE (demonstrativo_id, conta_id)
);

CREATE TABLE IF NOT EXISTS condominio.inadimplencia_mensal (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    demonstrativo_id BIGINT NOT NULL REFERENCES condominio.demonstrativo_mensal (id) ON DELETE CASCADE,
    competencia DATE NOT NULL,
    boletos_abertos INTEGER,
    unidades_inadimplentes INTEGER,
    percentual_unidades NUMERIC(7, 4),
    valor_sem_acrescimos NUMERIC(16, 2),
    regularizados_periodo INTEGER,
    novos_periodo INTEGER,
    dados_extras JSONB NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT ck_inadimplencia_competencia CHECK (date_trunc('month', competencia)::date = competencia),
    CONSTRAINT ck_inadimplencia_percentual CHECK (percentual_unidades IS NULL
        OR (percentual_unidades >= 0 AND percentual_unidades <= 100)),
    CONSTRAINT ck_inadimplencia_quantidades CHECK ((boletos_abertos IS NULL OR boletos_abertos >= 0)
        AND (unidades_inadimplentes IS NULL OR unidades_inadimplentes >= 0)
        AND (regularizados_periodo IS NULL OR regularizados_periodo >= 0)
        AND (novos_periodo IS NULL OR novos_periodo >= 0)),
    CONSTRAINT uq_inadimplencia UNIQUE (demonstrativo_id, competencia)
);

CREATE TABLE IF NOT EXISTS condominio.auditoria_extracao (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    documento_id BIGINT NOT NULL REFERENCES condominio.documento_importado (id) ON DELETE CASCADE,
    entidade_destino TEXT NOT NULL,
    registro_destino_id BIGINT,
    campo_destino TEXT NOT NULL,
    valor_extraido TEXT,
    pagina INTEGER,
    confianca NUMERIC(5, 4),
    coordenadas JSONB,
    revisado BOOLEAN NOT NULL DEFAULT FALSE,
    revisado_por TEXT,
    revisado_em TIMESTAMPTZ,
    observacao TEXT,
    CONSTRAINT ck_auditoria_pagina CHECK (pagina IS NULL OR pagina > 0),
    CONSTRAINT ck_auditoria_confianca CHECK (confianca IS NULL OR (confianca >= 0 AND confianca <= 1))
);

CREATE INDEX IF NOT EXISTS ix_unidade_condominio ON condominio.unidade (condominio_id);
CREATE INDEX IF NOT EXISTS ix_documento_metadados_gin ON condominio.documento_importado USING gin (metadados);
CREATE INDEX IF NOT EXISTS ix_boleto_unidade_competencia ON condominio.boleto (unidade_id, competencia DESC);
CREATE INDEX IF NOT EXISTS ix_boleto_condominio_vencimento ON condominio.boleto (condominio_id, data_vencimento DESC);
CREATE INDEX IF NOT EXISTS ix_boleto_status ON condominio.boleto (status) WHERE status <> 'PAGO';
CREATE INDEX IF NOT EXISTS ix_boleto_dados_extras_gin ON condominio.boleto USING gin (dados_extras);
CREATE INDEX IF NOT EXISTS ix_boleto_item_boleto ON condominio.boleto_item (boleto_id);
CREATE INDEX IF NOT EXISTS ix_demonstrativo_condominio_competencia
    ON condominio.demonstrativo_mensal (condominio_id, competencia DESC);
CREATE INDEX IF NOT EXISTS ix_categoria_pai ON condominio.categoria_financeira (categoria_pai_id);
CREATE INDEX IF NOT EXISTS ix_lancamento_demonstrativo ON condominio.lancamento_financeiro (demonstrativo_id);
CREATE INDEX IF NOT EXISTS ix_lancamento_categoria ON condominio.lancamento_financeiro (categoria_id);

CREATE OR REPLACE TRIGGER tg_administradora_atualizado BEFORE UPDATE ON condominio.administradora
    FOR EACH ROW EXECUTE FUNCTION condominio.fn_atualizar_timestamp();
CREATE OR REPLACE TRIGGER tg_condominio_atualizado BEFORE UPDATE ON condominio.condominio
    FOR EACH ROW EXECUTE FUNCTION condominio.fn_atualizar_timestamp();
CREATE OR REPLACE TRIGGER tg_unidade_atualizado BEFORE UPDATE ON condominio.unidade
    FOR EACH ROW EXECUTE FUNCTION condominio.fn_atualizar_timestamp();
CREATE OR REPLACE TRIGGER tg_pessoa_atualizado BEFORE UPDATE ON condominio.pessoa
    FOR EACH ROW EXECUTE FUNCTION condominio.fn_atualizar_timestamp();
CREATE OR REPLACE TRIGGER tg_boleto_atualizado BEFORE UPDATE ON condominio.boleto
    FOR EACH ROW EXECUTE FUNCTION condominio.fn_atualizar_timestamp();
CREATE OR REPLACE TRIGGER tg_validar_boleto_unidade BEFORE INSERT OR UPDATE OF unidade_id, condominio_id ON condominio.boleto
    FOR EACH ROW EXECUTE FUNCTION condominio.fn_validar_boleto_unidade();

CREATE OR REPLACE VIEW condominio.vw_boleto_resumo AS
SELECT b.id,
    c.nome AS condominio,
    c.cnpj,
    u.codigo AS unidade,
    u.bloco,
    p.nome AS pagador,
    b.competencia,
    b.data_vencimento,
    b.valor_original,
    b.valor_desconto,
    b.valor_original - b.valor_desconto AS valor_ate_desconto,
    b.valor_pago,
    b.status,
    b.linha_digitavel,
    b.numero_documento
FROM condominio.boleto b
JOIN condominio.condominio c ON c.id = b.condominio_id
JOIN condominio.unidade u ON u.id = b.unidade_id
LEFT JOIN condominio.pessoa p ON p.id = b.pagador_id;

CREATE OR REPLACE VIEW condominio.vw_demonstrativo_resumo AS
SELECT d.id,
    d.condominio_id,
    c.nome AS condominio,
    d.competencia,
    d.saldo_inicial,
    d.total_receitas,
    d.total_despesas,
    d.movimento_liquido,
    d.saldo_final,
    CASE WHEN d.total_receitas IS NULL OR d.total_despesas IS NULL THEN NULL::numeric
         ELSE d.total_receitas - d.total_despesas END AS movimento_calculado,
    CASE WHEN d.movimento_liquido IS NULL OR d.total_receitas IS NULL OR d.total_despesas IS NULL THEN NULL::numeric
         ELSE d.movimento_liquido - (d.total_receitas - d.total_despesas) END AS divergencia_movimento
FROM condominio.demonstrativo_mensal d
JOIN condominio.condominio c ON c.id = d.condominio_id;

COMMIT;

SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_schema = 'condominio'
ORDER BY table_type, table_name;