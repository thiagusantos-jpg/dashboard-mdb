# Política de Segurança - Mercado duBairro

## Versões Suportadas

| Versão | Suportada |
| ------ | --------- |
| 2.x    | ✅        |
| 1.x    | ❌        |

## Reportando Vulnerabilidades

Se você encontrar uma vulnerabilidade de segurança, **NÃO** abra uma issue pública.

### Como reportar:
1. Use a funcionalidade **"Report a vulnerability"** na aba Security deste repositório
2. Ou envie um email para: [seu-email@exemplo.com]

### O que incluir no report:
- Descrição da vulnerabilidade
- Passos para reproduzir
- Impacto potencial
- Sugestão de correção (se houver)

### Prazo de resposta:
- **24h** para confirmar recebimento
- **72h** para avaliação inicial
- **7 dias** para correção de vulnerabilidades críticas

## Práticas de Segurança

- Credenciais e chaves de API **nunca** são commitadas no repositório
- Variáveis sensíveis são gerenciadas via `.env` (protegido pelo `.gitignore`)
- Dependências são monitoradas pelo Dependabot
- Secret scanning está ativo para detectar vazamentos
