# MeritLense — Azure Deployment Guide

## Architecture

```
                         Internet
                            |
                     meritlense.com (Hostinger DNS)
                            |
               +------------+------------+
               |                         |
    www.meritlense.com          api.meritlense.com
    Azure Static Web Apps       Azure App Service
    (Next.js static export)     (Docker container)
               |                         |
               |    NEXT_PUBLIC_API_URL   |
               +-------------------------+
                                         |
                                 Azure Database for
                                 PostgreSQL Flexible Server
                                         |
                                 Azure Blob Storage
                                 (media files)
```

| Component | Azure Service | SKU | Domain |
|-----------|--------------|-----|--------|
| Backend API | App Service (Linux, Docker) | B1 | `api.meritlense.com` |
| Database | PostgreSQL Flexible Server | Burstable B1ms | — |
| Frontend | Static Web Apps | Free | `www.meritlense.com` / `meritlense.com` |
| Container Registry | Azure Container Registry | Basic | — |
| Media Storage | Azure Blob Storage | Standard | — |
| DNS | Hostinger (no transfer) | — | `meritlense.com` |

---

## Prerequisites

- [Azure CLI](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli) installed
- [Docker](https://docs.docker.com/get-docker/) installed (for local testing)
- GitHub repository with the project code
- Azure subscription

Log in to Azure CLI:
```bash
az login
```

---

## Step 1: Create Azure Resources

Set your variables (customize these):
```bash
RESOURCE_GROUP="meritlense-rg"
LOCATION="westeurope"
ACR_NAME="meritlenseacr"          # must be globally unique, lowercase, alphanumeric only
APP_SERVICE_PLAN="meritlense-plan"
APP_SERVICE_NAME="meritlense-api"
PG_SERVER="meritlense-pg"
PG_ADMIN_USER="pgadmin"
PG_ADMIN_PASSWORD="<generate-a-strong-password>"
DB_NAME="meritlense_db"
```

### 1.1 Resource Group
```bash
az group create --name $RESOURCE_GROUP --location $LOCATION
```

### 1.2 Azure Container Registry
```bash
az acr create \
  --resource-group $RESOURCE_GROUP \
  --name $ACR_NAME \
  --sku Basic \
  --admin-enabled true
```

### 1.3 App Service Plan
```bash
az appservice plan create \
  --name $APP_SERVICE_PLAN \
  --resource-group $RESOURCE_GROUP \
  --is-linux \
  --sku B1
```

### 1.4 App Service
```bash
az webapp create \
  --resource-group $RESOURCE_GROUP \
  --plan $APP_SERVICE_PLAN \
  --name $APP_SERVICE_NAME \
  --deployment-container-image-name $ACR_NAME.azurecr.io/meritlense-api:latest
```

### 1.5 Connect App Service to ACR
```bash
az webapp config container set \
  --name $APP_SERVICE_NAME \
  --resource-group $RESOURCE_GROUP \
  --container-image-name $ACR_NAME.azurecr.io/meritlense-api:latest \
  --container-registry-url https://$ACR_NAME.azurecr.io \
  --container-registry-user $(az acr credential show -n $ACR_NAME --query username -o tsv) \
  --container-registry-password $(az acr credential show -n $ACR_NAME --query passwords[0].value -o tsv)
```

### 1.6 PostgreSQL Flexible Server
```bash
az postgres flexible-server create \
  --resource-group $RESOURCE_GROUP \
  --name $PG_SERVER \
  --location $LOCATION \
  --admin-user $PG_ADMIN_USER \
  --admin-password $PG_ADMIN_PASSWORD \
  --sku-name Standard_B1ms \
  --tier Burstable \
  --storage-size 32 \
  --version 16 \
  --yes
```

### 1.7 Create the Database
```bash
az postgres flexible-server db create \
  --resource-group $RESOURCE_GROUP \
  --server-name $PG_SERVER \
  --database-name $DB_NAME
```

### 1.8 Firewall — Allow Azure Services
```bash
az postgres flexible-server firewall-rule create \
  --resource-group $RESOURCE_GROUP \
  --name $PG_SERVER \
  --rule-name AllowAzureServices \
  --start-ip-address 0.0.0.0 \
  --end-ip-address 0.0.0.0
```

### 1.9 Static Web Apps (one per environment)
```bash
for ENV in dev qa frontend; do
  az staticwebapp create \
    --name meritlense-$ENV \
    --resource-group $RESOURCE_GROUP \
    --location $LOCATION \
    --sku Free
done
```

### 1.10 App Services for Dev and QA

Reuse the same App Service Plan — each environment gets its own App Service:
```bash
for ENV in dev qa; do
  az webapp create \
    --resource-group $RESOURCE_GROUP \
    --plan $APP_SERVICE_PLAN \
    --name meritlense-api-$ENV \
    --deployment-container-image-name $ACR_NAME.azurecr.io/meritlense-api:latest

  az webapp config container set \
    --name meritlense-api-$ENV \
    --resource-group $RESOURCE_GROUP \
    --container-image-name $ACR_NAME.azurecr.io/meritlense-api:latest \
    --container-registry-url https://$ACR_NAME.azurecr.io \
    --container-registry-user $(az acr credential show -n $ACR_NAME --query username -o tsv) \
    --container-registry-password $(az acr credential show -n $ACR_NAME --query passwords[0].value -o tsv)
done
```

### 1.11 Databases for Dev and QA
```bash
for ENV in dev qa; do
  az postgres flexible-server db create \
    --resource-group $RESOURCE_GROUP \
    --server-name $PG_SERVER \
    --database-name meritlense_${ENV}
done
```

---

## Step 2: Configure App Service Environment Variables

```bash
az webapp config appsettings set \
  --resource-group $RESOURCE_GROUP \
  --name $APP_SERVICE_NAME \
  --settings \
    DJANGO_SETTINGS_MODULE=meritlense.settings.production \
    DJANGO_SECRET_KEY="$(python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')" \
    ALLOWED_HOSTS="$APP_SERVICE_NAME.azurewebsites.net" \
    CSRF_TRUSTED_ORIGINS="https://$APP_SERVICE_NAME.azurewebsites.net,https://www.meritlense.com" \
    CORS_ALLOWED_ORIGINS="https://www.meritlense.com" \
    DB_NAME="$DB_NAME" \
    DB_USER="$PG_ADMIN_USER" \
    DB_PASSWORD="$PG_ADMIN_PASSWORD" \
    DB_HOST="$PG_SERVER.postgres.database.azure.com" \
    DB_PORT="5432" \
    EMAIL_BACKEND="smtp" \
    EMAIL_HOST="smtp.gmail.com" \
    EMAIL_PORT="465" \
    EMAIL_HOST_USER="<your-email>" \
    EMAIL_HOST_PASSWORD="<your-app-password>" \
    DEFAULT_FROM_EMAIL="MeritLense <your-email>" \
    FRONTEND_URL="https://www.meritlense.com" \
    STRIPE_PUBLISHABLE_KEY="" \
    STRIPE_SECRET_KEY="" \
    STRIPE_WEBHOOK_SECRET="" \
    AZURE_STORAGE_CONNECTION_STRING="" \
    AZURE_STORAGE_CONTAINER_NAME="meritlense-media" \
    AZURE_QUEUE_CONNECTION_STRING="" \
    AZURE_DEFAULT_QUEUE_NAME="meritlense-jobs" \
    WEBSITES_PORT=8000
```

> **Important:** `WEBSITES_PORT=8000` tells Azure which port your container listens on.

Set health check:
```bash
az webapp config set \
  --resource-group $RESOURCE_GROUP \
  --name $APP_SERVICE_NAME \
  --generic-configurations '{"healthCheckPath": "/api/v1/health"}'
```

---

## Step 3: GitHub Environments & Secrets

### 3.1 Create GitHub Environments

Go to GitHub repo > Settings > Environments. Create three environments:

| Environment | Branch Policy | Approval Required |
|-------------|--------------|-------------------|
| `dev` | `dev` branch only | No |
| `qa` | `qa` branch only | No |
| `production` | `main` branch only | Yes (add yourself as reviewer) |

The production environment requires manual approval — when code is pushed to `main`, the deploy job will pause and wait for you to approve in GitHub Actions.

### 3.2 Repository Secrets (shared across all environments)

Go to Settings > Secrets and variables > Actions > Repository secrets:

| Secret Name | How to Get It |
|------------|--------------|
| `ACR_USERNAME` | `az acr credential show -n meritlenseacr --query username -o tsv` |
| `ACR_PASSWORD` | `az acr credential show -n meritlenseacr --query passwords[0].value -o tsv` |
| `AZURE_CREDENTIALS` | See command below |

Generate `AZURE_CREDENTIALS`:
```bash
az ad sp create-for-rbac \
  --name "meritlense-github-actions" \
  --role contributor \
  --scopes /subscriptions/<your-subscription-id>/resourceGroups/meritlense-rg \
  --json-auth
```
Copy the entire JSON output as the secret value.

### 3.3 Environment-Specific Secrets

For each environment, go to Settings > Environments > (env name) > Add secret:

| Environment | Secret Name | How to Get It |
|-------------|------------|--------------|
| `dev` | `SWA_TOKEN_DEV` | `az staticwebapp secrets list --name meritlense-dev -g meritlense-rg --query "properties.apiKey" -o tsv` |
| `qa` | `SWA_TOKEN_QA` | `az staticwebapp secrets list --name meritlense-qa -g meritlense-rg --query "properties.apiKey" -o tsv` |
| `production` | `SWA_TOKEN_PROD` | `az staticwebapp secrets list --name meritlense-frontend -g meritlense-rg --query "properties.apiKey" -o tsv` |

### 3.4 CI/CD Pipeline Flow

```
dev branch push ──▶ Build Docker ──▶ Deploy to meritlense-api-dev
                                      Deploy to meritlense-dev (SWA)

qa branch push  ──▶ Build Docker ──▶ Deploy to meritlense-api-qa
                                      Deploy to meritlense-qa (SWA)

main branch push ──▶ Build Docker ──▶ ⏸ Manual Approval ──▶ Deploy to meritlense-api
                                                              Deploy to meritlense-frontend (SWA)
```

### 3.5 Branching Strategy

```bash
# Create the branches
git checkout -b dev
git push -u origin dev

git checkout -b qa
git push -u origin qa

git checkout main
```

Workflow: `dev` → PR to `qa` → PR to `main` (prod)

---

## Step 4: Deploy

### First deployment (manual)

Build and push the Docker image manually to verify everything works:

```bash
# Log in to ACR
az acr login --name $ACR_NAME

# Build and push
docker build -t $ACR_NAME.azurecr.io/meritlense-api:latest .
docker push $ACR_NAME.azurecr.io/meritlense-api:latest

# Restart the App Service to pull the new image
az webapp restart --name $APP_SERVICE_NAME --resource-group $RESOURCE_GROUP
```

### Automated deployment (CI/CD)

After the first manual deploy, every push to `main` triggers GitHub Actions:
- **Backend changes** (api/, meritlense/, requirements/, Dockerfile) → builds Docker image, pushes to ACR, deploys to App Service
- **Frontend changes** (MeritLense-ui/) → builds Next.js static export, deploys to Static Web Apps

---

## Step 5: Post-Deploy Setup

### Create superuser
```bash
az webapp ssh --name $APP_SERVICE_NAME --resource-group $RESOURCE_GROUP
# Inside the container:
python manage.py createsuperuser
```

### Run database migrations (if needed manually)
```bash
az webapp ssh --name $APP_SERVICE_NAME --resource-group $RESOURCE_GROUP
python manage.py migrate
```

### Check logs
```bash
az webapp log tail --name $APP_SERVICE_NAME --resource-group $RESOURCE_GROUP
```

### Verify health
```bash
curl https://api.meritlense.com/api/v1/health
```

---

## Environment Variable Reference

### Backend (App Service)

| Variable | Required | Description |
|----------|----------|-------------|
| `DJANGO_SETTINGS_MODULE` | Yes | Must be `meritlense.settings.production` |
| `DJANGO_SECRET_KEY` | Yes | Strong random key — app crashes if missing |
| `ALLOWED_HOSTS` | Yes | Comma-separated hostnames (e.g. `meritlense-api.azurewebsites.net`) |
| `CSRF_TRUSTED_ORIGINS` | Yes | Comma-separated origins with `https://` prefix |
| `CORS_ALLOWED_ORIGINS` | Yes | Frontend origin(s) with `https://` prefix |
| `DB_NAME` | Yes | PostgreSQL database name |
| `DB_USER` | Yes | PostgreSQL username |
| `DB_PASSWORD` | Yes | PostgreSQL password |
| `DB_HOST` | Yes | PostgreSQL hostname (`*.postgres.database.azure.com`) |
| `DB_PORT` | Yes | PostgreSQL port (default `5432`) |
| `WEBSITES_PORT` | Yes | Must be `8000` |
| `EMAIL_BACKEND` | No | `smtp` or `console` (default `smtp`) |
| `EMAIL_HOST` | No | SMTP server hostname |
| `EMAIL_PORT` | No | SMTP port (`465` for SSL, `587` for TLS) |
| `EMAIL_HOST_USER` | No | SMTP username |
| `EMAIL_HOST_PASSWORD` | No | SMTP password / app password |
| `DEFAULT_FROM_EMAIL` | No | Sender address |
| `FRONTEND_URL` | No | Frontend URL for email links |
| `STRIPE_PUBLISHABLE_KEY` | No | Stripe publishable key |
| `STRIPE_SECRET_KEY` | No | Stripe secret key |
| `STRIPE_WEBHOOK_SECRET` | No | Stripe webhook signing secret |
| `AZURE_STORAGE_CONNECTION_STRING` | No | Azure Blob Storage connection string |
| `AZURE_STORAGE_CONTAINER_NAME` | No | Blob container name (default `meritlense-media`) |
| `AZURE_QUEUE_CONNECTION_STRING` | No | Azure Queue Storage connection string |
| `AZURE_DEFAULT_QUEUE_NAME` | No | Queue name (default `meritlense-jobs`) |
| `SECURE_SSL_REDIRECT` | No | Set to `False` to disable HTTPS redirect (default `True`) |

### Frontend (GitHub Variables for CI/CD)

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_API_URL` | Backend API URL (e.g. `https://api.meritlense.com/api/v1`) |
| `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` | Stripe publishable key |

---

## Custom Domain Setup (Hostinger DNS)

The domain `meritlense.com` stays registered at Hostinger. We only add DNS records to point subdomains at Azure.

### 6.1 Backend — `api.meritlense.com`

**In Azure Portal:**
1. Go to App Service > `meritlense-api` > Custom domains > Add custom domain
2. Enter `api.meritlense.com`
3. Azure will show you a TXT validation record

**In Hostinger DNS Zone:**
| Type | Name | Value |
|------|------|-------|
| TXT | `asuid.api` | *(Azure provides this verification ID)* |
| CNAME | `api` | `meritlense-api.azurewebsites.net` |

After DNS propagates (~5 min), click Validate in Azure. Azure auto-provisions a free SSL cert.

### 6.2 Frontend — `www.meritlense.com` and `meritlense.com`

**In Azure Portal:**
1. Go to Static Web App > `meritlense-frontend` > Custom domains > Add
2. Add `www.meritlense.com` first — Azure shows a validation token

**In Hostinger DNS Zone:**
| Type | Name | Value |
|------|------|-------|
| TXT | `_dnsauth.www` | *(Azure validation token for www)* |
| CNAME | `www` | `<your-swa>.azurestaticapps.net` |
| TXT | `_dnsauth` | *(Azure validation token for apex)* |
| A | `@` | *(Azure provides an IP for apex domain)* |

> **Note:** Apex domain (`meritlense.com` without www) requires an A record. Azure Static Web Apps provides the IP during custom domain setup.

### 6.3 Update App Service Environment Variables

After custom domains are active:
```bash
az webapp config appsettings set \
  --resource-group meritlense-rg \
  --name meritlense-api \
  --settings \
    ALLOWED_HOSTS="api.meritlense.com,meritlense-api.azurewebsites.net" \
    CSRF_TRUSTED_ORIGINS="https://www.meritlense.com,https://meritlense.com" \
    CORS_ALLOWED_ORIGINS="https://www.meritlense.com,https://meritlense.com" \
    FRONTEND_URL="https://www.meritlense.com"
```

### 6.4 Update Frontend Build Variable

In GitHub > Settings > Variables:
| Variable | Value |
|----------|-------|
| `NEXT_PUBLIC_API_URL` | `https://api.meritlense.com/api/v1` |

Then trigger a frontend rebuild (push a change to `MeritLense-ui/` or manually re-run the workflow).

---

## Troubleshooting

### Container won't start
```bash
az webapp log tail --name meritlense-api --resource-group meritlense-rg
```
Common causes: missing `DJANGO_SECRET_KEY`, database connection refused (check firewall rules), wrong `WEBSITES_PORT`.

### Database connection errors
- Ensure the firewall rule allows Azure services (0.0.0.0)
- Verify `DB_HOST` ends with `.postgres.database.azure.com`
- Production settings enforce `sslmode=require` — Azure PostgreSQL requires SSL

### Static files not loading (admin panel)
- Verify `collectstatic` ran during Docker build (check build logs)
- WhiteNoise serves static files automatically in production

### Frontend API calls failing
- Check `CORS_ALLOWED_ORIGINS` includes the exact frontend origin
- Check `CSRF_TRUSTED_ORIGINS` includes the exact frontend origin
- Verify `NEXT_PUBLIC_API_URL` was set correctly during frontend build

### Email not sending
- Set `EMAIL_BACKEND=console` to debug (emails print to App Service logs)
- Gmail SMTP may be blocked on some networks — port 465 (SSL) is more reliable than 587 (TLS)
