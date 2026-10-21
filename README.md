# 🚀 Consulta Vanguard - Automação Sistema Corban (Telefone ➔ CPF)

Automação desenvolvida em Python e Playwright para busca em lote de clientes no portal **Sistema Corban** (`gestao.sistemacorban.com.br`), convertendo números de **Telefone** em seus respectivos **CPFs**, com rotação automática de contas ao atingir limites diários e persistência resiliente.

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

- 🔍 **Busca por Telefone:** Aciona a janela de consulta rápida do Corban, digita o telefone e extrai o CPF diretamente da tabela de resultados.
- 🔄 **Rotação Automática de Logins:** Ao detectar que uma conta atingiu o limite diário de consultas, o sistema encerra a sessão com segurança, loga na próxima conta configurada e retenta o mesmo telefone sem interromper o fluxo.
- ⏱️ **Respeito ao Intervalo do Corban:** Aplica a pausa obrigatória de **10 segundos** entre pesquisas para evitar bloqueios do servidor.
- 💾 **Checkpoints e Histórico:** Grava periodicamente os resultados encontrados no arquivo CSV e registra os telefones consultados em um arquivo de histórico para que execuções futuras pulem o que já foi processado.
- 🛡️ **Segurança de Credenciais:** As credenciais e configurações sensíveis ficam isoladas no arquivo `.env`, protegido e ignorado pelo Git via `.gitignore`.

---

## 🏛️ Arquitetura do Projeto (Deep Modules)

O projeto foi estruturado seguindo o padrão de **Deep Modules** (*Módulos Profundos*), garantindo interfaces públicas enxutas e alto encapsulamento:

```
Consulta_Vanguard/
├── README.md                          # Documentação completa da aplicação
└── main/
    ├── .env                           # Credenciais e parâmetros reais (ignorado pelo Git)
    ├── .env.example                   # Modelo público de configuração
    ├── .gitignore                     # Proteção de arquivos confidenciais (*.csv, *.txt, .env)
    ├── consulta_corban.py             # Ponto de entrada CLI (~50 linhas)
    ├── telefones-base safira-menos juros.csv  # Arquivo de entrada
    └── core/
        ├── __init__.py
        ├── config.py                  # AppConfig: Leitura embutida de .env e parâmetros
        ├── domain.py                  # Modelos e funções puras (Credencial, Telefone, CPF)
        ├── storage.py                 # PhoneRepository: Leitura/escrita CSV, XLSX e histórico
        ├── corban_client.py           # CorbanClient: Adapter Playwright (login, modais, rotação)
        └── engine.py                  # ConsultaEngine: Orquestrador da fila, 10s e checkpoints
```

### Papel de cada módulo:
1. **`core/config.py` (`AppConfig`):**
   - Carrega as variáveis de `.env` sem dependências externas.
   - Fornece a lista de credenciais ordenadas e resolve caminhos de arquivos.
2. **`core/domain.py`:**
   - Define a dataclass imutável `Credencial` (com máscara de senha em logs).
   - Valida e formata números de telefone e CPFs (`000.000.000-00`).
   - Define exceções de domínio como `LimiteConsultasExcedido` e `ErroParadaImediata`.
3. **`core/storage.py` (`PhoneRepository`):**
   - Lê os telefones do arquivo de entrada (CSV ou XLSX), deduplica e pula o cabeçalho.
   - Consulta o histórico `telefones_consultados.txt` para pular o que já foi feito.
   - Salva novos resultados com garantia de cabeçalho e escrita em UTF-8 com BOM.
4. **`core/corban_client.py` (`CorbanClient`):**
   - Controla o navegador Playwright via Context Manager (`with CorbanClient(...) as client:`).
   - Abre o modal rápido, preenche os dados e raspa o CPF retornado.
   - Possui o método `alternar_conta(nova_credencial)` para troca dinâmica de sessão.
5. **`core/engine.py` (`ConsultaEngine`):**
   - Conduz o loop principal de processamento da fila de pendentes.
   - Trata a troca automática de login ao receber `LimiteConsultasExcedido`.
   - Gerencia checkpoints a cada 5 consultas e intervalo obrigatório de 10s.

---

## 📦 Pré-requisitos

- **Python:** Versão 3.10 ou superior.
- **Navegador Chromium do Playwright:** Gerenciado automaticamente pela biblioteca.

---

## 🔧 Instalação e Configuração

1. Abra o terminal (PowerShell no Windows) na pasta do projeto:
   ```powershell
   cd c:\Users\User2\Documents\Consulta_Vanguard\main
   ```

2. Instale as dependências necessárias:
   ```powershell
   pip install playwright openpyxl
   ```

3. Instale os navegadores do Playwright (se for a primeira vez):
   ```powershell
   playwright install chromium
   ```

---

## 🔐 Configuração de Credenciais (.env)

Crie ou edite o arquivo `main/.env` (baseado no `main/.env.example`). 

As contas configuradas em `CORBAN_CONTAS` serão utilizadas na ordem em que aparecem. Quando a primeira conta bater o limite diário, a automação rotaciona imediatamente para a segunda, e assim sucessivamente:

```env
# ============================================================
# CREDENCIAIS DO SISTEMA CORBAN
# ============================================================
# Lista de contas em ordem de prioridade (usuario:senha separados por vírgula):
CORBAN_CONTAS=ti@37277:Safira@26,Gestor@37277:Vanguard@@,Supervisao@37277:Safira@26

# ============================================================
# ARQUIVOS E PARÂMETROS DE EXECUÇÃO
# ============================================================
ARQUIVO_ENTRADA=telefones-base safira-menos juros.csv
ARQUIVO_SAIDA=resultados_telefones.csv
ARQUIVO_HISTORICO=telefones_consultados.txt

# Visualização do navegador (true = janela visível, false = headless em segundo plano)
MODO_VISIVEL=true

# Pausa obrigatória entre consultas (segundos)
TEMPO_ENTRE_BUSCAS=10

# Limite de tempo aguardando a resposta da pesquisa (segundos)
TIMEOUT_RESPOSTA=20

# Limite de telefones a processar por execução
LIMITE_REGISTROS=5000

# Frequência de gravação no arquivo CSV de saída (a cada N pesquisas)
INTERVALO_CHECKPOINT=5
```

---

## 📁 Arquivos de Entrada e Saída

### 1. Entrada (`telefones-base safira-menos juros.csv` ou `telefones.csv`)
- Arquivo CSV ou XLSX contendo uma coluna de telefones.
- A primeira linha (cabeçalho) é sempre ignorada automaticamente.
- Telefones podem ter ou não DDD, máscaras `(XX) XXXXX-XXXX` ou código de país `+55`.

### 2. Saída (`resultados_telefones.csv`)
Arquivo CSV gerado com duas colunas separadas e cabeçalho:
```csv
Telefone,CPF
(21) 99447-4558,053.302.057-35
(83) 8124-6146,123.456.789-00
```

### 3. Histórico (`telefones_consultados.txt`)
Lista de todos os telefones já consultados (mesmo os que não retornaram CPF). Garante que interrupções manuais (`Ctrl+C`) ou quedas de conexão permitam retomar exatamente de onde parou sem retrabalho.

---

## 🚀 Como Executar

Basta rodar o comando abaixo no terminal dentro da pasta `main`:

```powershell
python consulta_corban.py
```

### O que esperar durante a execução:
1. O navegador é iniciado e efetua login na primeira conta (`ti@37277`).
2. Para cada telefone pendente, o modal de consulta rápida é aberto e pesquisado.
3. Se um cliente for encontrado, o CPF é extraído e logado no terminal:
   ```text
   [★ ENCONTRADO] Telefone: (21) 99447-4558 | CPF: 053.302.057-35
   ```
4. Se a conta atual atingir o limite diário de consultas:
   ```text
   [LIMITE DIÁRIO ATINGIDO] Limite diário atingido na conta 'ti@37277'.
   🔄 Alternando para próxima conta [2/3]: 'Gestor@37277'...
   ✔ Nova conta pronta! Retestando o telefone (21) 99447-4558...
   ```
5. A cada 5 consultas, um checkpoint é salvo no arquivo CSV de saída.
6. Uma contagem regressiva de 10 segundos é respeitada antes de chamar a próxima busca.

---

## 🛡️ Tratamento de Erros e Segurança

- **Parada Segura (`ErroParadaImediata`):** Caso ocorra um erro de rede, timeout de clique ou falha inesperada no Playwright, o script é interrompido imediatamente **sem** registrar o telefone no arquivo de histórico, assegurando que o número seja reprocessado na próxima execução.
- **Interrupção Manual (`Ctrl+C`):** Se o usuário cancelar a execução a qualquer momento, o buffer de resultados é gravado no CSV e os recursos do navegador são liberados de forma limpa.
- **Privacidade de Dados:** Arquivos de clientes (`*.csv`), logs e históricos (`*.txt`) e arquivos de credenciais (`.env`) estão configurados no `.gitignore` para nunca serem enviados a repositórios públicos.
