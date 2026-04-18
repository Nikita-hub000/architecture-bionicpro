import React, { useEffect, useState } from 'react';

const API_URL = process.env.REACT_APP_API_URL ?? 'http://localhost:8000';

type ReportPayload = Record<string, unknown>;

type ReportResponse =
  | { cdnUrl: string; meta?: Record<string, unknown> }
  | ReportPayload;

const ReportPage: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [authLoading, setAuthLoading] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<ReportPayload | null>(null);
  const [cdnUrl, setCdnUrl] = useState<string | null>(null);

  const refreshSession = async () => {
    try {
      setAuthLoading(true);
      const res = await fetch(`${API_URL}/api/session`, { credentials: 'include' });
      const data = (await res.json()) as { authenticated?: boolean };
      setAuthenticated(Boolean(data?.authenticated));
    } catch {
      setAuthenticated(false);
    } finally {
      setAuthLoading(false);
    }
  };

  useEffect(() => {
    void refreshSession();
  }, []);

  const handleLogin = () => {
    window.location.href = `${API_URL}/login`;
  };

  const handleSwitchAccount = () => {
    window.location.href = `${API_URL}/switch-account`;
  };

  const downloadReport = async () => {
    try {
      setLoading(true);
      setError(null);
      setReport(null);
      setCdnUrl(null);

      const response = await fetch(`${API_URL}/api/reports`, {
        method: 'GET',
        credentials: 'include',
      });

      if (response.status === 401) {
        await refreshSession();
        handleLogin();
        return;
      }

      const data = (await response.json()) as ReportResponse;

      if (!response.ok) {
        setError(typeof (data as any)?.detail === 'string' ? ((data as any).detail as string) : `HTTP ${response.status}`);
        return;
      }

      if (typeof (data as any)?.cdnUrl === 'string') {
        setCdnUrl((data as any).cdnUrl as string);
        setReport(data as any);
      } else {
        setReport(data as ReportPayload);
      }

      await refreshSession();
    } catch (err) {
      setError('Ошибка доступа');
    } finally {
      setLoading(false);
    }
  };

  const authButtonText = authLoading ? 'Проверка сессии...' : authenticated ? 'Сменить аккаунт' : 'Войти';
  const authButtonHandler = authenticated ? handleSwitchAccount : handleLogin;

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">
      <div className="p-8 bg-white rounded-lg shadow-md w-full max-w-2xl">
        <h1 className="text-2xl font-bold mb-6">BionicPRO Reports</h1>

        <div className="flex gap-4 mb-6">
          <button
            onClick={downloadReport}
            className="px-4 py-2 bg-green-500 text-white rounded hover:bg-green-600"
            disabled={loading}
          >
            Download Report
          </button>

          <button
            onClick={authButtonHandler}
            className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
            disabled={loading || authLoading}
          >
            {authButtonText}
          </button>
        </div>

        {error && <div className="mt-4 text-red-500">{error}</div>}

        {cdnUrl && (
          <div className="mt-4">
            <div className="font-semibold mb-2">Отчёт сохранён и доступен по ссылке (CDN)</div>
            <a className="text-blue-600 underline break-all" href={cdnUrl} target="_blank" rel="noreferrer">
              {cdnUrl}
            </a>
          </div>
        )}

        {report && (
          <div className="mt-4">
            <h2 className="text-lg font-semibold mb-2">Полученный отчёт</h2>
            <pre className="bg-gray-900 text-gray-100 p-4 rounded overflow-auto text-sm">
              {JSON.stringify(report, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
};

export default ReportPage;