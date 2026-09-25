import React from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

const TutorPage = React.lazy(() => import('../pages/TutorPage'));
const ProgressDashboardPage = React.lazy(() => import('../pages/ProgressDashboardPage'));
const AdminObservabilityPage = React.lazy(() => import('../pages/AdminObservabilityPage'));
const OnboardingPage = React.lazy(() => import('../pages/OnboardingPage'));

/** Quiet placeholder while a route's chunk loads — no flash of branding. */
const RouteFallback = () => (
  <div className="h-[100dvh] grid place-items-center bg-canvas">
    <div className="flex items-center gap-2" role="status" aria-live="polite">
      {[0, 1, 2].map(i => (
        <span
          key={i}
          className="dot-pulse w-1.5 h-1.5 rounded-full bg-ink-3"
          style={{ animationDelay: `${i * 0.16}s` }}
        />
      ))}
      <span className="sr-only">Loading</span>
    </div>
  </div>
);

export default function AppRoutes() {
  return (
    <React.Suspense fallback={<RouteFallback />}>
      <Routes>
        {/* The tutor is the product — it opens straight into a chat. */}
        <Route path="/" element={<TutorPage />} />
        <Route path="/tutor" element={<TutorPage />} />

        <Route path="/progress" element={<ProgressDashboardPage />} />
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="/admin/observability" element={<AdminObservabilityPage />} />

        <Route path="*" element={<Navigate to="/tutor" replace />} />
      </Routes>
    </React.Suspense>
  );
}
