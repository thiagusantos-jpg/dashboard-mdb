# Avaliação de provedor — Open Finance (Task 14)

**Provedor escolhido:** Stone Open Banking (API própria da Stone, não um agregador terceiro).
**Fonte:** https://docs.openbank.stone.com.br/ (consultado em 2026-09-12).
**Decisão:** integrar diretamente a API da Stone. Justificativa: a Mercado duBairro já opera com Stone como adquirente/conta de pagamento; a Stone expõe uma API de Open Banking própria (não depende de aprovação de um agregador terceiro) e os endpoints necessários (saldo, extrato) já estão documentados e confirmados abaixo.

## Checklist de pré-condição (item por item)

| Item | Suportado? | Evidência |
|---|---|---|
| Conta Stone PJ | ✅ | A API opera sobre "Payment Account" (conta de pagamento) da própria Stone — `/docs/guias/conta-de-pagamento/`. |
| CNPJ | ✅ Implícito | Contas de pagamento Stone são majoritariamente PJ; a API não segrega por tipo de documento nos endpoints consultados. |
| Saldos | ✅ Confirmado | `GET /api/v1/accounts/{account_id}/balance` → `{balance, blocked_balance, scheduled_balance}`, valores em centavos (inteiros). |
| Transações | ✅ Confirmado | `GET /api/v1/accounts/{account_id}/statement` → paginado por cursor (`before`/`after`/`limit`), filtro por `type` e por `start_datetime`/`end_datetime`. Item traz `id`, `operation` (debit/credit), `amount`, `fee_amount`, `balance_before`, `balance_after`, `status`, `created_at`. |
| Histórico | ✅ | O parâmetro `start_datetime`/`end_datetime` do extrato permite consultar qualquer janela histórica disponível na conta. |
| Webhooks | ✅ Confirmado | Eventos assíncronos (transferências, Pix, aprovação de consentimento etc.), corpo cifrado (JWE: RSA-OAEP-256 + A256GCM) e assinado (JWS RS256, chaves públicas em `/api/v1/discovery/keys`). Header `x-stone-webhook-event-id` para idempotência. Reenvio automático (até 50 tentativas/dia) se a resposta não for 2xx. |
| Consentimento | ✅ Confirmado | Fluxo de redirecionamento: link `https://conta.stone.com.br/consentimento?client_id=...&jwt=...`, JWT assinado RS256 (claims `type`, `client_id`, `redirect_uri`, `session_metadata`, `iss`, `aud`, `jti`, `iat`, `nbf`, `exp` ≤ 2h). Retorno via `redirect_uri` com `consent_result` (`approved`/`ignored`/`already_granted`) e `resource_id`. |
| Sandbox | ✅ Confirmado | Ambiente sandbox dedicado: `https://sandbox-api.openbank.stone.com.br` (API) e `https://sandbox.conta.stone.com.br` (consentimento) e `https://sandbox-accounts.openbank.stone.com.br` (token). |
| Preço | ⚠️ Não publicado | A documentação pública não informa preço; exige contato comercial via formulário (Pipefy) para credenciamento. **Pendência:** confirmar custo antes de habilitar em produção. |
| Exportação | ✅ Indireto | O extrato paginado (`statement`) cobre exportação de dados transacionais; não há um endpoint de "exportação em lote" dedicado nos guias consultados. |
| Encerramento (revogação de consentimento) | ⚠️ Não encontrado nos guias públicos | A documentação de consentimento descreve criação e aprovação, mas não expõe um endpoint público de revogação/status de consentimento. **Pendência:** confirmar com o time de integração da Stone (ou via formulário comercial) o endpoint de revogação antes de anunciar essa capacidade a usuários finais. Até lá, `revoke_consent()` marca a conexão local como revogada e para a sincronização — mas não garante que a Stone também invalidou o acesso do lado dela.

## Autenticação (confirmado)

- Token: `POST https://{sandbox-}accounts.openbank.stone.com.br/auth/realms/stone_bank/protocol/openid-connect/token`, `grant_type=client_credentials`, `client_assertion` = JWT RS256 assinado com a chave privada do desenvolvedor (`exp` ≤ 15 min), `client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`.
- Uso: `Authorization: Bearer <token>` + `User-Agent` obrigatório em toda chamada autenticada.

## Decisão de implementação

Dado que os itens de **preço** e **encerramento/revogação** não estão publicamente documentados, a integração é implementada com:
1. O contrato `OpenFinanceProvider` (protocolo neutro), testável sem credenciais reais.
2. Um adapter real `StoneOpenFinanceProvider` contra os endpoints confirmados acima (token, consentimento, saldo, extrato).
3. `revoke_consent()` como uma operação **local** (para a sincronização, preserva o histórico já importado) — sem depender de um endpoint de revogação da Stone que não está documentado publicamente.
4. Nenhuma chamada real é feita nos testes automatizados — eles usam um provedor falso (`FakeOpenFinanceProvider`) que implementa o mesmo protocolo, validando o comportamento do serviço (ciclo de vida da conexão, idempotência, preservação de saldo em falha) sem exigir credenciais da Stone.

Antes de habilitar em produção, é necessário: (a) preencher o formulário comercial da Stone para obter `client_id` e registrar a chave pública, (b) confirmar preço, e (c) confirmar o processo de revogação de consentimento do lado da Stone.
