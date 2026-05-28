import React from 'react';
import ReportPage from './components/ReportPage';

const App: React.FC = () => {
    return (
        <div className="App">
            {/* Теперь нам не нужен Provider, так как сессией управляет браузер и BFF */}
            <ReportPage />
        </div>
    );
};

export default App;