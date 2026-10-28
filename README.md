# 🚀 Consulta Margem Corban - Automação por CPF

Automação desenvolvida em Python e Playwright para consulta em lote de margem disponível e banco por **CPF** no portal **Sistema Corban** (`gestao.sistemacorban.com.br`), com rotação automática de contas ao atingir limites diários e persistência resiliente.

---

## 📋 Sumário
- [Recursos Principais](#-recursos-principais)
- [Arquitetura do Projeto (Deep Modules)](#-arquitetura-do-projeto-deep-modules)
- [Pré-requisitos](#-pré-requisitos)
- [Instalação e Configuração](#-instalação-e-configuração)
- [Configuração de Credenciais (.env)](#-configuração-de-credenciais-env)
- [Arquivos de Entrada e Saída](#-arquivos-de-entrada-e-saída)
- [Como Executar](#-como-executar)
- [Tratamento de Erros e Segurança](#-tratamento-de-erros-e-segurança)

---

## ✨ Recursos Principais

- 🔍 **Busca por CPF:** Consulta rápida de clientes no Corban por CPF e extração automática de dados dos benefícios.
- 💰 **Extração de Margem e Banco:** Coleta precisa do Banco (formato `NOME - 000`) e da Margem Livre Disponível (monetária limpa com sinal e decimais em vírgula).
- 🔄 **Rotação Automática de Logins:** Ao detectar que uma conta atingiu o limite diário de consultas, encerra a sessão com segurança, loga na próxima conta configurada e retenta o mesmo CPF.
- 💾 **Checkpoints e Histórico:** Grava periodicamente os resultados encontrados no arquivo CSV e pula CPFs que já constem no arquivo de saída para evitar reprocessamento desnecessário.
- 🛡️ **Segurança de Credenciais e Dados:** Credenciais e caminhos dos arquivos CSV ficam isolados no `.env`, protegido e ignorado pelo Git via `.gitignore`. Nenhum dado pessoal (CSV) é enviado ao GitHub.

---

## 🏗️ Arquitetura do Projeto (Deep Modules)

O projeto foi estruturado seguindo os princípios de **Deep Modules** (módulos com interfaces enxutas e alto poder de implementação interna):

```text
Consulta_Vanguard/
├── main/
│   ├── core/
│   │   ├── __init__.py          # Pacote de módulos centrais
│   │   ├── config.py            # Módulo de Configuração (AppConfig e leitura de .env)
│   │   ├── domain.py            # Módulo de Domínio (Credencial, ResultadoMargem, regras puras)
│   │   ├── corban_client.py     # Adapter Playwright (Automação, navegação e extração)
│   │   ├── storage.py           # Repositório de Dados (Leitura de CPFs e escrita no CSV)
│   │   └── engine.py            # Motor de Execução (Orquestração, checkpoints e rotação)
│   ├── consulta_corban.py       # Ponto de entrada (CLI enxuto)
│   └── .env.example             # Modelo de configuração para criação do .env local
├── .gitignore                   # Ignora dados sensíveis, logs e arquivos CSV
├── pyrightconfig.json           # Configuração de Source Root para a IDE
├── requirements.txt             # Dependências na raiz
└── README.md                    # Documentação do projeto
```

### Seams e Responsabilidades dos Módulos:
1. **`domain.py`**: Modelos imutáveis e funções puras de validação/limpeza (`limpar_cpf`, `formatar_cpf`, `limpar_banco`, `limpar_margem`). Não possui dependências externas de I/O.
2. **`config.py`**: Carrega credenciais do `.env` com suporte a múltiplas contas, caminhos de arquivos e parâmetros de tempo.
3. **`storage.py`**: Gerencia a leitura de arquivos de entrada (CSV ou Excel) e persistência do CSV de saída com cabeçalho `CPF;Banco;Margem_Disponivel`.
4. **`corban_client.py`**: Encapsula todo o ciclo de vida do Playwright, lida com sessões já abertas (`dialog.accept`), login resiliente e extração web.
5. **`engine.py`**: Conecta repositório e cliente, administrando o fluxo, fila de pendentes, checkpoints e troca de contas.

---

## 📦 Pré-requisitos

- **Python**: versão 3.10 ou superior (testado no Python 3.12).
- **Gerenciador de Pacotes**: `pip`.
- **Navegador Playwright**: Chromium (instalado via CLI do Playwright).

---

## 🚀 Instalação e Configuração

1. Abra o terminal na raiz do projeto e crie/ative um ambiente virtual (opcional, mas recomendado):
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

2. Instale as dependências:
   ```powershell
   pip install -r requirements.txt
   ```

3. Instale os navegadores do Playwright (se for a primeira vez):
   ```powershell
   playwright install chromium
   ```

---

## 🔑 Configuração de Credenciais (.env)

Crie um arquivo `.env` dentro da pasta `main/` (ou na raiz) copiando o modelo de `main/.env.example`:

```env
# Lista de contas para rotação automática
CORBAN_CONTAS=ti@37277:Senha1,Gestor@37277:Senha2,Supervisao@37277:Senha3

# URL do portal
SITE_URL=https://gestao.sistemacorban.com.br

# Arquivos de dados (devem ficar fora do GitHub)
ARQUIVO_ENTRADA=cpfs.csv
ARQUIVO_SAIDA=resultados.csv

# Parâmetros
MODO_VISIVEL=true
TEMPO_ESPERA=2
TIMEOUT_RESPOSTA=20
LIMITE_CPFS=500
INTERVALO_CHECKPOINT=10
```

> ⚠️ **Atenção:** O arquivo `.env` contém senhas e é automaticamente ignorado pelo `.gitignore`. Nunca commite seu `.env` nem arquivos `.csv` com dados de clientes.

---

## 📄 Arquivos de Entrada e Saída

- **Entrada (`ARQUIVO_ENTRADA`)**: Arquivo `.csv` ou `.xlsx` contendo a lista de CPFs na 1ª coluna.
- **Saída (`ARQUIVO_SAIDA`)**: Arquivo `.csv` gerado automaticamente com os resultados obtidos, contendo as colunas:
  - `CPF`: CPF formatado (`000.000.000-00`)
  - `Banco`: Nome e código do banco (ex: `BANCO BMG - 318`)
  - `Margem_Disponivel`: Valor da margem disponível (ex: `125,50` ou `-74,14`)

---

## ▶️ Como Executar

Dentro da pasta `main/`:

```powershell
python consulta_corban.py
```

O script:
1. Lerá as configurações do `.env`.
2. Validará a base de entrada e identificará CPFs pendentes.
3. Abrirá o portal Corban, autenticará e iniciará a consulta por CPF.
4. Salvará periodicamente os resultados a cada checkpoint configurado.
5. Rotacionará as contas automaticamente caso uma delas atinja o limite diário.

---

## 🛡️ Tratamento de Erros e Segurança

- **Tolerância a Sessão Prévia**: O cliente Playwright confirma automaticamente mensagens do tipo "Usuário já conectado", garantindo que novas tentativas de login não travem o processo.
- **Parada Segura**: Em caso de interrupção pelo usuário (`Ctrl+C`), os resultados pendentes da memória são descarregados imediatamente no CSV de saída.
