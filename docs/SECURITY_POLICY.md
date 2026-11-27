# Fronteiras de segurança

Sandbox Codex no workspace com rede restrita, sem segredos do GitHub expostos em flags. Host controlador executa mutações GitHub tipadas usando conta do usuário e verifica remote/HEAD/base. `developer` não aprova nem faz merge. O mesmo login não pode gerar `APPROVE` formal distinto; usar parecer técnico independente SHA-bound como comentário, e proteger merge via controlador.

Nunca persistir nem publicar candidato, diversidade, currículos, cookies, tokens de sessão, hashes de senhas, emails reais ou respostas pessoais do usuário. O produto local guardará dados consentidos sob proteção; só fixtures sintéticas no repo.

Não burlar robots, termos, CAPTCHA, MFA, testes de avaliação, ou enviar candidaturas não autorizadas. Replays após clique incerto exigem reconciliação. Autorreparo do runner pode alterar somente código e testes do runner; não pode mudar política de segurança para passar.
