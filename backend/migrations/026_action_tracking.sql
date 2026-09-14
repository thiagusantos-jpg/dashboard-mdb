-- How big the problem behind an action was when it was created (170 produtos sem
-- estoque), so the Central de Ações can show how it moved since. NULL for older
-- rows and for actions that are not about a count (one product's price, a manual task).
ALTER TABLE actions ADD COLUMN baseline_count INTEGER;
