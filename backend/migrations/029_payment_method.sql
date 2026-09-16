-- Como cada conta é paga: boleto (localizado pelo DDA do banco), Pix, débito automático
-- ou transferência. O código guarda a linha digitável ou o Pix copia e cola, sem validar
-- dígito verificador: serve para conferir valor/vencimento e copiar na hora de pagar.
ALTER TABLE financial_entries ADD COLUMN payment_method TEXT NOT NULL DEFAULT '';
ALTER TABLE financial_entries ADD COLUMN payment_code TEXT NOT NULL DEFAULT '';
ALTER TABLE financial_recurrences ADD COLUMN payment_method TEXT NOT NULL DEFAULT '';
ALTER TABLE financial_recurrences ADD COLUMN payment_code TEXT NOT NULL DEFAULT '';
