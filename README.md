# 🚀 Consulta Corban V2 - Filtro Idade & Empréstimos por CPF

Automação desenvolvida em Python e Playwright para consulta e qualificação em lote de clientes por **CPF** no portal **Sistema Corban** (`gestao.sistemacorban.com.br`), aplicando regras de elegibilidade por benefício, desanonimização de contatos e rotação automática de credenciais.

---

## 📋 Sumário
- [Regras de Filtro e Negócio](#-regras-de-filtro-e-negócio)
- [Recursos Principais](#-recursos-principais)
- [Arquitetura do Projeto (Deep Modules)](#-arquitetura-do-projeto-deep-modules)
- [Pré-requisitos](#-pré-requisitos)
- [Instalação e Configuração](#-instalação-e-configuração)
- [Configuração de Credenciais (.env)](#-configuração-de-credenciais-env)
- [Arquivos de Entrada e Saída](#-arquivos-de-entrada-e-saída)
- [Como Executar](#-como-executar)
- [Tratamento de Erros e Segurança](#-tratamento-de-erros-e-segurança)

---

## 🎯 Regras de Filtro e Negócio

Para cada benefício vinculado ao CPF, o sistema avalia os seguintes critérios de corte:

1. 🎂 **Filtro de Idade:** O cliente deve possuir menos de **80 anos** (`IDADE_LIMITE=80`). A idade é calculada dinamicamente a partir do campo `#cliNascimento`.
2. 💳 **Filtro de Empréstimo:** O último contrato de empréstimo deve possuir **até 10 parcelas pagas** (`PARCELAS_PAGAS_LIMITE=10`). Clientes sem empréstimo são pulados quando `PULAR_SE_SEM_EMPRESTIMO=true`.
3. 📞 **Desanonimização de Telefones:** Dispara a liberação dos números ocultos (`#btnHideOrShowTelefone` ou `abrirCamposTelefones()`), capturando e formatando **Tel 1** e **Tel 2**.

---

## ✨ Recursos Principais

- 🔄 **Rotação Automática de Logins:** Ao detectar limite diário em uma conta, encerra a sessão com segurança, loga na próxima conta configurada e retenta o mesmo CPF.
- 💾 **Histórico e Prevenção de Reprocessamento:** Mantém um arquivo de histórico (`cpfs_consultados.txt`) para registrar todos os CPFs já verificados (aprovados ou reprovados pelos filtros), garantindo que execuções futuras processem apenas novos registros.
- 🛡️ **Segurança de Dados Sensíveis:** Arquivos `.env`, logs e planilhas `.csv` não são versionados no Git via `.gitignore`, protegendo a privacidade dos dados.

---

## 🏗️ Arquitetura do Projeto (Deep Modules)

```text
Consulta_Vanguard/
├── main/
│   ├── core/
│   │   ├── __init__.py          # Inicialização do pacote core
│   │   ├── config.py            # Módulo de Configuração (AppConfig, filtros e .env)
│   │   ├── domain.py            # Domínio (ResultadoClienteFiltro, idade, formatação)
│   │   ├── corban_client.py     # Automação Playwright (Navegação, desanonimização e extração)
│   │   ├── storage.py           # Repositório de Dados (Leitura, histórico e saída CSV)
│   │   └── engine.py            # Motor de Execução (Fila, rotação de contas e checkpoints)
│   ├── consulta_corban.py       # Ponto de entrada CLI
│   └── .env.example             # Modelo para criação do .env local
├── .gitignore                   # Ignora dados sensíveis, logs e arquivos CSV
├── pyrightconfig.json           # Configuração de Source Root para a IDE
├── requirements.txt             # Dependências unificadas do projeto
└── README.md                    # Documentação do projeto
```

---

## 📦 Pré-requisitos

- **Python**: versão 3.10 ou superior.
- **Playwright Chromium**: instalado via CLI.

---

## 🚀 Instalação e Configuração

1. Crie e ative um ambiente virtual:
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

2. Instale as dependências:
   ```powershell
   pip install -r requirements.txt
   ```

3. Instale os binários do Playwright:
   ```powershell
   playwright install chromium
   ```

---

## 🔑 Configuração de Credenciais (.env)

Crie o arquivo `.env` dentro da pasta `main/` (ou na raiz) com base no `.env.example`:

```env
# Lista de contas para rotação automática
CORBAN_CONTAS=ti@37277:Senha1,Gestor@37277:Senha2,Supervisao@37277:Senha3

# URL do portal
SITE_URL=https://gestao.sistemacorban.com.br

# Arquivos de dados
ARQUIVO_ENTRADA=cpfs.csv
ARQUIVO_SAIDA=resultados.csv
ARQUIVO_HISTORICO=cpfs_consultados.txt

# Regras de Elegibilidade
IDADE_LIMITE=80
PARCELAS_PAGAS_LIMITE=10
PULAR_SE_SEM_EMPRESTIMO=true

# Parâmetros de Execução
MODO_VISIVEL=true
TEMPO_ESPERA=2
TIMEOUT_RESPOSTA=20
LIMITE_CPFS=500
INTERVALO_CHECKPOINT=5
```

---

## 📄 Arquivos de Entrada e Saída

- **Entrada (`ARQUIVO_ENTRADA`)**: Arquivo `.csv` ou `.xlsx` com CPFs na 1ª coluna.
- **Saída (`ARQUIVO_SAIDA`)**: CSV gerado com os clientes aprovados em ambos os filtros:
  - `CPF`: CPF formatado (`000.000.000-00`)
  - `Idade`: Idade em anos inteiros
  - `Tel 1`: Primeiro telefone desanonimizado e formatado
  - `Tel 2`: Segundo telefone desanonimizado e formatado
  - `Parcelas Pagas`: Quantidade de parcelas pagas no último empréstimo
- **Histórico (`ARQUIVO_HISTORICO`)**: Arquivo `.txt` contendo a lista de CPFs já verificados.

---

## ▶️ Como Executar

Dentro da pasta `main/`:

```powershell
python consulta_corban.py
```
