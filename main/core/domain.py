import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Credencial:
    """Representa uma credencial de acesso ao portal Sistema Corban."""

    usuario: str
    senha: str


@dataclass
class ResultadoMargem:
    """Representa o resultado da extração de margem de um cliente."""

    cpf: str
    banco: str
    margem_disponivel: str

    def para_dicionario(self) -> dict[str, str]:
        """Converte o resultado para o formato esperado no arquivo CSV de saída."""
        return {
            "CPF": self.cpf,
            "Banco": self.banco,
            "Margem_Disponivel": self.margem_disponivel,
        }


class LimiteConsultasExcedido(Exception):
    """Lançada quando a conta ativa atinge o limite diário de consultas do portal."""

    pass


class ErroParadaImediata(Exception):
    """Lançada em situações críticas que demandam interrupção segura da execução."""

    pass


def limpar_cpf(cpf: str) -> str:
    """Remove pontuação e caracteres não numéricos, completando com zeros à esquerda."""
    digitos = "".join(filter(str.isdigit, str(cpf)))
    if digitos and len(digitos) <= 11:
        return digitos.zfill(11)
    return digitos


def formatar_cpf(cpf: str) -> str:
    """Formata o CPF no padrão visual 000.000.000-00."""
    c = limpar_cpf(cpf)
    if len(c) == 11:
        return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"
    return cpf


def limpar_banco(texto: str) -> str:
    """Extrai apenas a string limpa do banco no formato NOME - 000."""
    if not texto or texto.strip() in ("N/A", ""):
        return "N/A"

    m = re.search(
        r"([A-ZÁÉÍÓÚÀÂÊÔÃÕÇÜa-záéíóúàâêôãõçü0-9\s\.\(\)\/,&\'_]+?)\s*[-–]\s*(\d{3})",
        texto,
        re.IGNORECASE,
    )
    if m:
        nome = m.group(1).strip()
        codigo = m.group(2).strip()
        return f"{nome} - {codigo}".upper()
    return texto.strip().upper()


def limpar_margem(texto: str) -> str:
    """Extrai estritamente o valor monetário numérico (ex: -74,14), convertendo pontos em vírgulas."""
    if not texto or texto.strip() in ("N/A", ""):
        return "N/A"

    m = re.search(
        r"([-–—]?)\s*(?:R\$\s*)?([-–—]?)\s*(\d+(?:[.,]\d+)*)", texto, re.IGNORECASE
    )
    if m:
        s1 = m.group(1).strip()
        s2 = m.group(2).strip()

        sinal = "-" if (s1 in ["-", "–", "—"] or s2 in ["-", "–", "—"]) else ""
        numero = m.group(3)

        if "." in numero and "," in numero:
            numero = numero.replace(".", "")
        elif "." in numero and "," not in numero:
            numero = numero.replace(".", ",")

        return f"{sinal}{numero}"

    return "N/A"
