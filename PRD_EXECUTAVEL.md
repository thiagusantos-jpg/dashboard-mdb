# 📋 PRD EXECUTÁVEL - DUBAIRRO MVP

> Documento histórico de março/2026. Para a execução atual, seguir o [plano atualizado de integração direta com o Mobne](/Users/thiagosantos/Documents/DASHBOARD_MDB/PLANO_IMPLEMENTACAO_MOBNE.md), definido após a análise aprovada em 11/09/2026. O novo fluxo deve operar sem importação Excel; stack, etapas e estimativas abaixo não representam compromissos atuais.
**Status:** Ready for Implementation
**Timeline:** 7 dias em 5 FASES
**Equipe:** 1 Full-stack Developer
**Data:** Março 2026

---

## 🎯 DECISÕES CRÍTICAS CONFIRMADAS

| Questão | Resposta | Justificativa |
|---------|----------|---------------|
| **Database** | PostgreSQL via Supabase | Escala + relatórios avançados + free tier |
| **Timeline** | 7 dias (MVP) | Foco em core features |
| **Equipe** | 1 full-stack | Stack enxuto e opinionado |
| **Auth** | Credenciais (dev) → Supabase Auth (prod) | Rápido no início, seguro no final |
| **Sync Mobne** | Automática 1h (cron) | Simples, zero delay aceitável |
| **Frontend** | React 18 + Vite | Deploy rápido + HMR |

---

## 🏗️ ARQUITETURA DO PROJETO

```
dubairro/
├── frontend/                           # React 18 + Vite
│   ├── src/
│   │   ├── components/
│   │   │   ├── Auth/
│   │   │   │   ├── LoginForm.jsx
│   │   │   │   └── ProtectedRoute.jsx
│   │   │   ├── Dashboard/
│   │   │   │   ├── DashboardLayout.jsx
│   │   │   │   ├── DataTable.jsx
│   │   │   │   └── SyncStatus.jsx
│   │   │   ├── Upload/
│   │   │   │   ├── ExcelUploader.jsx
│   │   │   │   └── UploadProgress.jsx
│   │   │   └── Reports/
│   │   │       ├── SalesReport.jsx
│   │   │       └── MetricsChart.jsx
│   │   ├── pages/
│   │   │   ├── LoginPage.jsx
│   │   │   ├── DashboardPage.jsx
│   │   │   ├── UploadPage.jsx
│   │   │   └── ReportsPage.jsx
│   │   ├── services/
│   │   │   ├── api.js           # API client (Axios)
│   │   │   ├── supabaseClient.js # Supabase auth
│   │   │   └── mobneSync.js      # Mobne integration
│   │   ├── hooks/
│   │   │   ├── useAuth.js
│   │   │   └── useSyncStatus.js
│   │   ├── context/
│   │   │   └── AuthContext.jsx
│   │   ├── App.jsx
│   │   └── main.jsx
│   ├── vite.config.js
│   ├── tailwind.config.js
│   └── package.json
│
├── backend/                            # Vercel Edge Functions
│   ├── api/
│   │   ├── auth/
│   │   │   ├── login.js         # POST /api/auth/login
│   │   │   ├── validate.js      # GET /api/auth/validate
│   │   │   └── logout.js        # POST /api/auth/logout
│   │   ├── upload/
│   │   │   └── excel.js         # POST /api/upload/excel
│   │   ├── data/
│   │   │   ├── list.js          # GET /api/data/list
│   │   │   ├── search.js        # GET /api/data/search
│   │   │   └── export.js        # GET /api/data/export
│   │   ├── sync/
│   │   │   ├── mobne.js         # POST /api/sync/mobne
│   │   │   └── status.js        # GET /api/sync/status
│   │   └── reports/
│   │       ├── sales.js         # GET /api/reports/sales
│   │       └── metrics.js       # GET /api/reports/metrics
│   ├── lib/
│   │   ├── supabase.js          # Supabase client
│   │   ├── auth.js              # Auth middleware
│   │   ├── mobneClient.js       # Mobne API client
│   │   ├── excelParser.js       # Excel parsing logic
│   │   └── utils.js             # Utilities
│   ├── middleware/
│   │   └── auth.js
│   ├── cron/
│   │   └── mobneSync.js         # Scheduled sync (1h)
│   └── vercel.json
│
├── database/
│   ├── schema.sql               # DB schema
│   ├── migrations/
│   │   └── 001_init.sql
│   └── seed.sql                 # Dev data
│
├── docs/
│   ├── API.md                   # API documentation
│   ├── ARCHITECTURE.md          # Architecture details
│   └── DEPLOYMENT.md            # Deployment guide
│
├── .env.example
├── .env.local (git ignored)
├── README.md
└── package.json (root)
```

---

## 📅 TIMELINE POR FASES

### ⚡ FASE 1: Setup & Infrastructure (Dias 1-2)

**Objetivo:** Configurar stack completo e banco de dados

#### Tarefas:
- [ ] Criar repositório + branches (dev, staging, main)
- [ ] Setup Supabase:
  - [ ] Criar conta Supabase + projeto
  - [ ] Gerar connection string
  - [ ] Executar schema inicial (users, data_uploads, sync_logs)
- [ ] Setup Vercel:
  - [ ] Conectar repositório
  - [ ] Configurar environment variables
  - [ ] Habilitar Edge Functions
- [ ] Setup Frontend (Vite):
  ```bash
  npm create vite@latest frontend -- --template react
  cd frontend
  npm install
  npm install -D tailwindcss postcss autoprefixer
  npx tailwindcss init -p
  ```
- [ ] Setup Backend:
  - [ ] Criar estrutura /api
  - [ ] Instalar dependências (supabase-js, axios, bcrypt, jsonwebtoken)
  - [ ] Configurar .env.local

#### Deliverables:
- ✅ Supabase database pronto com schema
- ✅ Vercel deployment pipeline funcional
- ✅ Frontend Vite rodando em localhost:5173
- ✅ Backend Edge Functions testáveis

---

### 🔐 FASE 2: Autenticação & Dashboard MVP (Dias 2-3)

**Objetivo:** Usuários podem fazer login e ver dashboard básico

#### Backend:
- [ ] Implementar `/api/auth/login` (credenciais simples + JWT)
  - Validar credenciais no banco
  - Gerar JWT token
  - Retornar token + user data
- [ ] Implementar `/api/auth/validate` (middleware de proteção)
- [ ] Implementar `/api/auth/logout`
- [ ] Setup middleware de autenticação

#### Frontend:
- [ ] `LoginPage.jsx` (form simples)
- [ ] `AuthContext.jsx` (gerenciar token em memória)
- [ ] `useAuth.js` (hook para proteger rotas)
- [ ] `ProtectedRoute.jsx` (wrapper)
- [ ] Salvar token em localStorage

#### Database:
```sql
-- Tabela de usuários
CREATE TABLE users (
  id UUID PRIMARY KEY,
  email VARCHAR(255) UNIQUE NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  nome VARCHAR(255),
  role VARCHAR(50) DEFAULT 'user',
  created_at TIMESTAMP DEFAULT NOW()
);

-- Tabela de uploads
CREATE TABLE data_uploads (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  filename VARCHAR(255),
  data JSONB,
  uploaded_at TIMESTAMP DEFAULT NOW()
);

-- Tabela de sync
CREATE TABLE sync_logs (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  sync_type VARCHAR(50),
  status VARCHAR(50),
  last_sync TIMESTAMP,
  next_sync TIMESTAMP
);
```

#### Deliverables:
- ✅ Login funcional
- ✅ Dashboard vazio (só estrutura)
- ✅ Autenticação em produção (Vercel)

---

### 📤 FASE 3: Upload Excel & Data Display (Dias 4-5)

**Objetivo:** Usuários podem fazer upload de Excel e ver dados no dashboard

#### Backend:
- [ ] Implementar `/api/upload/excel`
  - Receber arquivo Excel
  - Parser com `xlsx` library
  - Validar estrutura
  - Salvar em Supabase (JSONB ou tabela relacional)
  - Retornar preview dos dados
- [ ] Implementar `/api/data/list`
  - Paginação
  - Filtros básicos
- [ ] Implementar `/api/data/search`
- [ ] Implementar `/api/data/export` (CSV)

#### Frontend:
- [ ] `ExcelUploader.jsx` (drag & drop)
- [ ] `UploadProgress.jsx` (barra de progresso)
- [ ] `DataTable.jsx` (mostrar dados com paginação)
- [ ] Integrar com API client

#### Database:
```sql
-- Tabela de dados (estrutura flexível)
CREATE TABLE raw_data (
  id UUID PRIMARY KEY,
  upload_id UUID REFERENCES data_uploads(id),
  user_id UUID REFERENCES users(id),
  data JSONB,
  created_at TIMESTAMP DEFAULT NOW(),
  INDEX idx_user_id (user_id)
);
```

#### Deliverables:
- ✅ Upload Excel funcionando
- ✅ Dados exibidos em tabela
- ✅ Export CSV

---

### 🔄 FASE 4: Integração Mobne & Sync (Dias 5-6)

**Objetivo:** Dados sincronizam automaticamente do Mobne a cada 1h

#### Backend:
- [ ] Implementar `mobneClient.js`
  - Autenticação com Mobne (API keys em .env)
  - Fetch dados de Mobne
  - Transformar formato
- [ ] Implementar `/api/sync/mobne` (endpoint manual)
- [ ] Implementar `/api/sync/status` (status da última sync)
- [ ] Setup cron job (Vercel Cron):
  ```js
  // api/cron/mobneSync.js
  export default async function handler(req, res) {
    if (req.headers['authorization'] !== `Bearer ${process.env.CRON_SECRET}`) {
      return res.status(401).json({ error: 'Unauthorized' });
    }

    // Executar sync
    await syncFromMobne();
    res.status(200).json({ success: true, timestamp: new Date() });
  }
  ```

#### Frontend:
- [ ] `SyncStatus.jsx` (mostrar última sincronização)
- [ ] Botão manual "Sincronizar agora"
- [ ] Indicador de progresso

#### Deliverables:
- ✅ Cron automático funcionando (a cada 1h)
- ✅ Dados do Mobne aparecendo no dashboard
- ✅ Status de sync visível

---

### 📊 FASE 5: Relatórios & Go-Live (Dias 6-7)

**Objetivo:** Deploy em produção com relatórios básicos

#### Backend:
- [ ] Implementar `/api/reports/sales` (agregações)
- [ ] Implementar `/api/reports/metrics` (KPIs)
- [ ] Otimizar queries (indexes, caching)

#### Frontend:
- [ ] `SalesReport.jsx` (tabela com totais)
- [ ] `MetricsChart.jsx` (gráficos simples com Chart.js)
- [ ] `ReportsPage.jsx` (agrupa relatórios)

#### Deploy:
- [ ] Migrar auth de credenciais simples → Supabase Auth
- [ ] Testes de carga (Vercel)
- [ ] Validar cron jobs
- [ ] Setup monitoring (Vercel Analytics)
- [ ] Deploy em main branch

#### Deliverables:
- ✅ App completo em produção
- ✅ Relatórios funcionando
- ✅ Sync automático validado

---

## ✅ CHECKLIST TÉCNICO

### Setup Inicial:
- [ ] Git branches (main, dev, staging)
- [ ] Supabase projeto criado
- [ ] Vercel conectado
- [ ] .env.local configurado

### Database:
- [ ] Schema criado
- [ ] Migrations rodadas
- [ ] Seed data inserido (test users)
- [ ] Indexes criados
- [ ] Backups configurados

### Backend:
- [ ] Edge Functions deployando
- [ ] Auth middleware funcional
- [ ] Cron jobs agendados
- [ ] Tratamento de erros implementado
- [ ] Rate limiting configurado

### Frontend:
- [ ] React Router setup
- [ ] Context API para auth
- [ ] Requisições HTTP interceptadas
- [ ] Loading states
- [ ] Error messages

### Segurança:
- [ ] JWTs com expiração (15min access, 7d refresh)
- [ ] CORS configurado
- [ ] Input validation (frontend + backend)
- [ ] SQL injection prevention (use prepared statements)
- [ ] Secrets em environment variables

### Performance:
- [ ] Code splitting em React
- [ ] Lazy loading de rotas
- [ ] Caching de dados estáticos
- [ ] Compressão de assets
- [ ] Paginated queries

### Testing:
- [ ] Testar fluxo completo (login → upload → sync → relatório)
- [ ] Testar erro de rede (retry logic)
- [ ] Testar cron job manualmente

---

## 🚀 PRÓXIMOS PASSOS

1. **Hoje:** Confirmar este PRD
2. **Amanhã:** Iniciar FASE 1 (Setup)
3. **Dia 7:** Deploy em produção

---

## 📚 REFERÊNCIAS RÁPIDAS

**Supabase Docs:** https://supabase.com/docs
**Vercel Edge Functions:** https://vercel.com/docs/functions
**React 18:** https://react.dev
**Vite:** https://vitejs.dev
**TailwindCSS:** https://tailwindcss.com

---

**Status:** ✅ PRONTO PARA IMPLEMENTAÇÃO
