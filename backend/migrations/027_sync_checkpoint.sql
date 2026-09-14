-- Vercel Hobby stops a function at 300 s, and one sync (two months of receipts
-- plus catalogs) did not fit: the request came back 504 and every retry started
-- over. A serverless run now works in steps and pauses before that limit; this
-- column keeps the step plan, what is done and the failed months, so the next
-- request resumes the same job instead of downloading everything again.
ALTER TABLE jobs ADD COLUMN checkpoint TEXT;
