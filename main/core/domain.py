from dataclasses import dataclass
import re


class ErroConsulta(Exception):
    """Exceção base para erros durante a consulta."""
    pass


class ErroParadaImediata(ErroConsulta):
    """Lançada quando ocorre erro crítico (Locator/Timeout) exigindo parada imediata sem marcar o telefone."""
    pass


class LimiteConsultasExcedido(ErroConsulta):
    """Lançada quando a busca atinge o limite do portal Corban."""
    pass


@dataclass(frozen=True)
class ResultadoConsulta:
    """Representa o resultado da pesquisa de um cliente."""
    telefone: str
    cpf: str

    def to_dict(self) -> dict[str, str]:
        return {
            "Telefone": self.telefone,
            "CPF": self.cpf,
        }


def limpar_telefone(tel: str) -> str:
    """Extrai apenas os dígitos numéricos e remove código 55 do Brasil se presente."""
    if not tel:
        return ""
    digitos = "".join(filter(str.isdigit, str(tel)))
    if (len(digitos) == 12 or len(digitos) == 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    return digitos


def formatar_telefone(tel: str) -> str:
    """Formata o telefone no padrão (XX) XXXXX-XXXX ou (XX) XXXX-XXXX."""
    digitos = limpar_telefone(tel)
    if len(digitos) == 11:
        return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
    elif len(digitos) == 10:
        return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"
    return str(tel).strip() if tel else ""


def limpar_cpf(cpf: str) -> str:
    """Remove caracteres não numéricos e preenche com zeros à esquerda até 11 dígitos."""
    digitos = "".join(filter(str.isdigit, str(cpf)))
    if digitos and len(digitos) <= 11:
        return digitos.zfill(11)
    return digitos


def formatar_cpf(cpf: str) -> str:
    """Formata CPF no padrão 000.000.000-00."""
    c = limpar_cpf(cpf)
    if len(c) == 11:
        return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"
    return cpf
