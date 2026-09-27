import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { AppShell } from './AppShell'
import { AssistantPage } from '../features/assistant/AssistantPage'
import { GridImpactPage } from '../features/grid/GridImpactPage'
import { GoogleSitePage } from '../features/google/GoogleSitePage'
import { AdminPage } from '../features/admin/AdminPage'
import { CallbackPage } from '../features/auth/CallbackPage'
import { LoginPage } from '../features/auth/LoginPage'
import { TwinPage } from '../features/twin/TwinPage'
import { useMe } from './useMe'
import { AreaPlannerPage } from '../features/area-planner/AreaPlannerPage'
import { DataCataloguePage } from '../features/data-catalog/DataCataloguePage'
import { OverviewPage } from '../features/overview/OverviewPage'
import { PlansPage } from '../features/planning/PlansPage'
import { RunPage } from '../features/planning/RunPage'
import { WorkspacePage } from '../features/workspace/WorkspacePage'

const rootRoute = createRootRoute({ component: AppShell })

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: OverviewPage,
})

const workspaceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/workspace',
  component: WorkspacePage,
})

const dataRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/data',
  component: DataCataloguePage,
})

const areaRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/areas/$regionId',
  component: function AreaRoute() {
    const { regionId } = areaRoute.useParams()
    return <AreaPlannerPage regionId={Number(regionId)} />
  },
})

const plansRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/plans',
  component: PlansPage,
})

const runRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/runs/$runId',
  component: function RunRoute() {
    const { runId } = runRoute.useParams()
    return <RunPage key={runId} runId={runId} />
  },
})

const gridRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/runs/$runId/grid',
  component: function GridRoute() {
    const { runId } = gridRoute.useParams()
    return <GridImpactPage runId={runId} />
  },
})

const googleSiteRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/runs/$runId/sites/$siteId/google',
  validateSearch: (search: Record<string, unknown>): { opt: string } => ({
    opt: String(search.opt ?? ''),
  }),
  component: function GoogleSiteRoute() {
    const { runId, siteId } = googleSiteRoute.useParams()
    const { opt } = googleSiteRoute.useSearch()
    return <GoogleSitePage runId={runId} siteId={siteId} optimisationId={opt} />
  },
})

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  component: LoginPage,
})
const callbackRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/auth/callback',
  component: CallbackPage,
})
const twinRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/twin',
  component: TwinPage,
})
const adminRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/admin',
  component: function AdminRoute() {
    const me = useMe().data
    return <AdminPage me={me} />
  },
})

const assistantRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/assistant',
  validateSearch: (search: Record<string, unknown>): { runId?: string } =>
    typeof search.runId === 'string' ? { runId: search.runId } : {},
  component: function AssistantRoute() {
    const { runId } = assistantRoute.useSearch()
    return <AssistantPage runId={runId} />
  },
})

const assistantSessionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/assistant/$sessionId',
  component: function AssistantSessionRoute() {
    const { sessionId } = assistantSessionRoute.useParams()
    return <AssistantPage sessionId={sessionId} />
  },
})

const routeTree = rootRoute.addChildren([
  indexRoute,
  workspaceRoute,
  areaRoute,
  plansRoute,
  runRoute,
  gridRoute,
  googleSiteRoute,
  assistantRoute,
  assistantSessionRoute,
  dataRoute,
  loginRoute,
  callbackRoute,
  twinRoute,
  adminRoute,
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
