import { createBrowserRouter, RouterProvider, NavLink, Outlet } from 'react-router';
import { useState, useEffect } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, useIsMobile } from '@databricks/appkit-ui/react';
import { Map, Bot, Menu } from 'lucide-react';
import { RegionsPage } from './pages/RegionsPage';
import { RegionDetailPage } from './pages/RegionDetailPage';
import { PlannerPage } from './pages/PlannerPage';

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
    isActive
      ? 'bg-[#FF3621] text-white'
      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
  }`;

const mobileNavLinkClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
    isActive
      ? 'bg-[#FF3621] text-white'
      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
  }`;

type NavLinkClassFn = (props: { isActive: boolean }) => string;

function NavLinks({
  className,
  linkClass,
  onClick,
}: {
  className?: string;
  linkClass: NavLinkClassFn;
  onClick?: () => void;
}) {
  return (
    <nav className={className}>
      <NavLink to="/" end className={linkClass} onClick={onClick}>
        <Map className="h-4 w-4" />
        Regions
      </NavLink>
      <NavLink to="/planner" className={linkClass} onClick={onClick}>
        <Bot className="h-4 w-4" />
        Planner AI
      </NavLink>
    </nav>
  );
}

function Layout() {
  const isMobile = useIsMobile();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  useEffect(() => {
    if (!isMobile) setMobileNavOpen(false);
  }, [isMobile]);

  return (
    <div className="min-h-screen bg-[#F9F7F4] flex flex-col">
      <header className="bg-[#0B2026] text-white px-4 md:px-6 py-3 flex items-center gap-4">
        <div className="flex items-center gap-2 mr-2">
          <div className="w-2 h-6 bg-[#FF3621] rounded-sm" />
          <h1 className="text-base font-semibold tracking-tight">Care Gap Atlas</h1>
        </div>

        <NavLinks className="hidden md:flex gap-1" linkClass={navLinkClass} />

        <div className="ml-auto md:hidden">
          <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
            <button
              onClick={() => setMobileNavOpen(true)}
              className="rounded p-1.5 hover:bg-white/10 transition-colors"
              aria-label="Open navigation"
            >
              <Menu className="h-5 w-5" />
            </button>
            <SheetContent side="left" className="bg-[#0B2026] text-white">
              <SheetHeader>
                <SheetTitle className="text-white">Care Gap Atlas</SheetTitle>
              </SheetHeader>
              <NavLinks
                className="flex flex-col gap-1 mt-4"
                linkClass={mobileNavLinkClass}
                onClick={() => setMobileNavOpen(false)}
              />
            </SheetContent>
          </Sheet>
        </div>
      </header>

      <main className="flex-1 p-4 md:p-6">
        <Outlet />
      </main>
    </div>
  );
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: '/', element: <RegionsPage /> },
      { path: '/region/:id', element: <RegionDetailPage /> },
      { path: '/planner', element: <PlannerPage /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
