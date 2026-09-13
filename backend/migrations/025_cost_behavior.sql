-- Break-even point from the cost center: each expense category is a fixed or
-- variable cost. NULL keeps the default for the category (see
-- backend/finance/accounts.py::default_cost_behavior), so existing rows need no
-- backfill and a later change of defaults applies to every untouched category.
ALTER TABLE finance_accounts ADD COLUMN cost_behavior TEXT;
