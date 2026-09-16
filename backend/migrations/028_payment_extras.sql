-- Juros e multa pagos junto com uma conta vencida: o acréscimo vira um lançamento
-- próprio (já quitado) em "Juros e multas", e o pagamento guarda quanto foi e qual
-- lançamento ele criou, para o estorno desfazer os dois juntos.
ALTER TABLE obligation_payments ADD COLUMN late_fee_cents BIGINT;
ALTER TABLE obligation_payments ADD COLUMN late_fee_entry_id BIGINT;
