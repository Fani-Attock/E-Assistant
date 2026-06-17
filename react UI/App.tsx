import React, { Suspense } from 'react';
import '@radix-ui/themes/styles.css';
import { Theme } from '@radix-ui/themes';
import { ToastContainer } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import './styles.css';

import Home from './src/pages/Home';
import History from './src/pages/History';
import About from './src/pages/About';
import NotFound from './src/pages/NotFound';
import Store from './src/pages/Store';
import StoreProduct from './src/pages/StoreProduct';
import Account from './src/pages/Account';
import SellerDashboard from './src/pages/SellerDashboard';
import ReportDetail from './src/pages/ReportDetail';
import { MarketplaceSessionProvider } from './src/lib/marketplaceSession';

const App: React.FC = () => {
  return (
    <Theme appearance="dark" radius="large" scaling="100%">
      <MarketplaceSessionProvider>
        <Router future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <Suspense fallback={
            <div className="min-h-screen bg-[#0d1117] flex items-center justify-center">
              <div className="w-8 h-8 rounded-full border-2 border-[#58a6ff] border-t-transparent animate-spin" />
            </div>
          }>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/store" element={<Store />} />
              <Route path="/store/products/:productId" element={<StoreProduct />} />
              <Route path="/account" element={<Account />} />
              <Route path="/seller/dashboard" element={<SellerDashboard />} />
              <Route path="/history" element={<History />} />
              <Route path="/reports/:reportId" element={<ReportDetail />} />
              <Route path="/about" element={<About />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </Suspense>
          <ToastContainer
            position="top-right"
            autoClose={3000}
            newestOnTop
            closeOnClick
            pauseOnHover
            theme="dark"
          />
        </Router>
      </MarketplaceSessionProvider>
    </Theme>
  );
};

export default App;
