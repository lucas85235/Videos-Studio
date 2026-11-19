"""Página de bloqueio, desafio ou erro no lugar da matéria (captura de página).

O site que desconfia do navegador automático devolve a verificação anti-robô (Cloudflare "Just a moment..." / "Um
momento…", "Confirme que é humano", Ray ID), um captcha, "Access denied", "429 Too Many Requests", a parede de
assinatura ou de consentimento, ou uma página vazia. Fotografada, ela virava a "prova" da fonte no vídeo (QA r4 do
prj_gq4krrs11w: 12 s da tela do Cloudflare sob o chip "GAMESBEAT" e o carimbo BOATO). `blocked_reason` diz, pelo
título, pelo texto da página e pelo status HTTP, se é uma dessas, em português, para o aviso do acervo.

Falso positivo derruba material bom: a matéria que só cita "Cloudflare" ou "captcha" (um golpe de captcha falso, uma
queda da Cloudflare) é uma página longa com o título dela. Por isso o texto de desafio só conta numa página curta
(`SHORT_PAGE_CHARS`, a de desafio tem poucas centenas de caracteres) e nunca quando a página traz o trecho esperado
(o título da matéria); o título de desafio também só conta quando é o título inteiro (não "Access denied: como ...").
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

SHORT_PAGE_CHARS = 2000
"""Texto visível acima disto é página de verdade (a de desafio do Cloudflare tem ~300 caracteres; uma matéria, milhares)."""
BLANK_PAGE_CHARS = 1
"""Menos letras que isto no corpo: a página não montou nada."""
EXPECTED_MIN_CHARS = 20
"""Trecho esperado curto demais ("GTA 6") não prova que a página é a matéria."""


def _norm(text: str) -> str:
    """Minúsculas, sem acento, aspas e reticências simples e espaços colapsados ("Um momento…" → "um momento...")."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("…", "...").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.lower().split())


# Título inteiro (sem o nome do site depois de "|", "-" ou "–") de página de desafio, bloqueio ou erro.
_TITLES = (
    # Cloudflare e afins ("Just a moment..." em cada língua)
    r"just a moment", r"um momento", r"un momento", r"un instant", r"einen moment", r"een moment",
    r"chwileczke", r"bir dakika", r"attention required!?", r"one more step", r"please wait", r"aguarde",
    r"por favor,? aguarde", r"espere un momento", r"veuillez patienter", r"bitte warten",
    r"checking your browser", r"verificando (o )?(seu )?navegador", r"security check", r"verificacao de seguranca",
    r"verificacion de seguridad", r"verification de securite", r"sicherheitsuberprufung", r"verifica di sicurezza",
    r"ddos-guard", r"ddos protection", r"human verification", r"bot verification", r"verify you are human",
    r"are you a (human|robot)\??", r"robot check", r"captcha", r"pardon our interruption", r"you have been blocked",
    r"request blocked", r"request rejected", r"access denied", r"acesso negado", r"acceso denegado", r"acces refuse",
    r"zugriff verweigert", r"accesso negato", r"forbidden", r"\d{3} forbidden", r"error \d{3,4}", r"erro \d{3,4}",
    r"too many requests", r"\d{3} too many requests", r"rate limited", r"service unavailable",
    r"\d{3} service (temporarily )?unavailable", r"bad gateway", r"\d{3} bad gateway", r"gateway time-?out",
    r"internal server error", r"\d{3} internal server error", r"404", r"404 not found", r"not found",
    r"page not found", r"pagina nao encontrada", r"pagina no encontrada", r"page introuvable", r"seite nicht gefunden",
    r"before you continue( to [\w .]+)?", r"antes de continuar( para o [\w .]+)?", r"bevor sie (zu [\w .]+ )?weitergehen",
    r"avant de continuer", r"subscribe to continue( reading)?", r"assine para continuar( lendo)?",
)
_TITLE_RE = re.compile(r"^(?:" + "|".join(_TITLES) + r")[\s.!]*$")

# Frases do corpo de uma página de desafio, bloqueio, captcha ou parede (só contam em página curta).
_TEXT_MARKERS = (
    # Cloudflare (desafio gerenciado, JS e Turnstile), nas línguas comuns
    "verify you are human", "verifying you are human", "verify that you are human", "confirm you are human",
    "confirme que e humano", "confirme que voce e humano", "verifique se voce e humano", "verificando se voce e humano",
    "verifica que eres humano", "confirma que eres humano", "verifiez que vous etes humain",
    "confirmez que vous etes humain", "bestatigen sie, dass sie ein mensch sind", "verifica di essere umano",
    "checking your browser before accessing", "checking if the site connection is secure",
    "verificando se a conexao do site e segura", "comprobando si la conexion del sitio es segura",
    "needs to review the security of your connection", "precisa revisar a seguranca da sua conexao",
    "precisa analisar a seguranca da sua conexao", "performing security verification",
    "executando verificacao de seguranca", "realizando verificacion de seguridad",
    "performance & security by cloudflare", "performance and security by cloudflare",
    "desempenho e seguranca da cloudflare", "desempenho e seguranca por cloudflare",
    "rendimiento y seguridad de cloudflare", "performances et securite par cloudflare",
    "this website uses a security service to protect", "this website is using a security service to protect",
    "este site utiliza um servico de seguranca", "este site usa um servico de seguranca",
    "este sitio utiliza un servicio de seguridad", "enable javascript and cookies to continue",
    "ative o javascript e os cookies para continuar", "habilite javascript y las cookies para continuar",
    "please turn javascript on and reload the page", "ddos protection by", "please complete the security check",
    "complete a verificacao de seguranca", "why have i been blocked", "por que fui bloqueado", "sorry, you have been blocked",
    "voce foi bloqueado", "has sido bloqueado", "vous avez ete bloque", "cloudflare ray id",
    # captcha e outros guardas (reCAPTCHA, hCaptcha, PerimeterX, DataDome, Imperva/Distil, Akamai)
    "i'm not a robot", "i am not a robot", "nao sou um robo", "no soy un robot", "je ne suis pas un robot",
    "ich bin kein roboter", "press & hold", "press and hold to confirm", "pressione e segure", "manten pulsado",
    "complete the captcha", "solve the captcha", "resolva o captcha", "please solve this captcha",
    "are you a robot", "voce e um robo", "eres un robot", "unusual traffic from your computer network",
    "trafego incomum", "trafico inusual", "something about your browser made us think you were a bot",
    "pardon our interruption", "incapsula incident id", "request unsuccessful", "access to this page has been denied",
    "datadome", "px-captcha",
    # acesso negado, limite de pedidos, erro do servidor
    "access denied", "acesso negado", "acceso denegado", "acces refuse", "zugriff verweigert",
    "you don't have permission to access", "you do not have permission to access", "voce nao tem permissao para acessar",
    "403 forbidden", "error 1020", "error 1015", "error 1010", "error 1006", "you are being rate limited",
    "too many requests", "muitas solicitacoes", "demasiadas solicitudes", "request blocked",
    "the request could not be satisfied", "502 bad gateway", "503 service unavailable", "504 gateway time",
    "500 internal server error", "web server is down", "origin is unreachable",
    # parede de assinatura ou de consentimento cobrindo a página
    "subscribe to continue reading", "subscribe to keep reading", "sign in to continue reading",
    "assine para continuar lendo", "para continuar lendo, assine", "faca login para continuar lendo",
    "conteudo exclusivo para assinantes", "this content is for subscribers only", "suscribete para seguir leyendo",
    "abonnez-vous pour lire la suite", "before you continue to", "antes de continuar para o",
)
_RAY_ID = re.compile(r"\bray id:?\s*[0-9a-f]{12,}\b")
_CHALLENGE_WORDS = ("cloudflare", "captcha", "turnstile", "hcaptcha", "recaptcha")

_STATUS_REASON = {401: "pede login", 403: "recusou o acesso", 407: "pede login no proxy", 429: "limitou os pedidos",
                  451: "está bloqueada", 404: "não existe", 410: "foi removida"}


def _short(text: str) -> bool:
    return len(text) <= SHORT_PAGE_CHARS


def _has_expected(text: str, expected: Iterable[str]) -> bool:
    """A página traz o trecho esperado (o título da matéria, sem o nome do veículo): é a matéria, não o desafio."""
    for raw in expected:
        for part in re.split(r"\s+[-–—|·]\s+|:\s+", raw or ""):
            part = _norm(part).strip(" .\"'")
            if len(part) >= EXPECTED_MIN_CHARS and part in text:
                return True
    return False


def blocked_reason(title: str | None, text: str | None, status: int | None = None, *,
                   expected: Iterable[str] = (), blank: bool = False) -> str | None:
    """Por que a página não é a página (o motivo, em português), ou None quando ela é de verdade.

    - `status`: o HTTP da última navegação da página (4xx e 5xx são bloqueio ou erro);
    - o título inteiro de desafio, bloqueio ou erro ("Just a moment...", "Um momento…", "Access denied", "404"),
      quando a página é curta;
    - o texto de desafio, captcha, bloqueio ou parede ("Confirme que é humano", "Ray ID: ...", "Access denied") numa
      página curta, sem o trecho esperado (`expected`);
    - `blank`: a página sem texto nenhum (só na captura ao vivo: as gravadas de antes não guardavam o texto)."""
    body = _norm(text or "")
    head = _norm(title or "")
    if isinstance(status, int) and not isinstance(status, bool) and status >= 400:
        why = _STATUS_REASON.get(status, "deu erro no servidor" if status >= 500 else "recusou o acesso")
        return f"a página {why} (HTTP {status})"
    if blank and len(body.replace(" ", "")) < BLANK_PAGE_CHARS:
        return "a página veio vazia (não carregou nada)"
    if not _short(body) or _has_expected(body, expected):
        return None
    # o título sem o nome do site ("Attention Required! | Cloudflare", "Access denied - Site")
    parts = [head, *(p.strip() for p in re.split(r"\s+[|–—-]\s+", head))] if head else []
    title_hit = any(p and _TITLE_RE.match(p) for p in parts)
    marker = next((m for m in _TEXT_MARKERS if m in body), None)
    ray = bool(_RAY_ID.search(body))
    if not (title_hit or marker or ray):
        return None
    robot = ("human", "humano", "robo", "robot", " bot", "captcha", "cloudflare", "security", "seguranca",
             "seguridad", "securite", "browser", "javascript", "press", "pressione", "traffic", "trafego", "trafico")
    if ray or any(w in head for w in _CHALLENGE_WORDS) or (marker and any(k in marker for k in robot)):
        return "o site mostrou a verificação anti-robô, captcha ou Cloudflare, no lugar da página"
    if marker and any(k in marker for k in ("assin", "subscri", "suscri", "abonn", "sign in", "login",
                                            "continue to", "continuar para")):
        return "uma parede de assinatura ou de consentimento cobriu a página"
    if marker or title_hit:
        return "o site devolveu uma página de bloqueio ou de erro no lugar da matéria"
    return None


__all__ = ["blocked_reason", "SHORT_PAGE_CHARS"]
