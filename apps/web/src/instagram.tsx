// Instagram nativo em Configurações: conectar contas profissionais (login ou token do painel da Meta),
// endereço de retorno do login, endereço público dos vídeos e limite de posts de cada conta.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Camera, ExternalLink, KeyRound, Trash2 } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import {
  api,
  formatWhen,
  type InstagramAccount,
  type InstagramAccountDetail,
  type InstagramLinkStart,
  type InstagramLinkStatus,
  type InstagramStatus,
} from './api';
import { Badge, Button, ErrorBox, Field, Input, Section, useConfirm, useToast } from './ui';

export const useInstagramStatus = (projectId?: string) =>
  useQuery({
    queryKey: ['instagram-status', projectId ?? ''],
    queryFn: () =>
      api.get<InstagramStatus>(
        projectId ? `/api/instagram/status?project_id=${encodeURIComponent(projectId)}` : '/api/instagram/status',
      ),
    refetchInterval: 15_000,
  });

const AccountRow: React.FC<{ account: InstagramAccount }> = ({ account }) => {
  const client = useQueryClient();
  const confirm = useConfirm();
  const detail = useQuery({
    queryKey: ['instagram-account', account.user_id],
    queryFn: () => api.get<InstagramAccountDetail>(`/api/instagram/accounts/${account.user_id}`),
    staleTime: 60_000,
    retry: false,
  });
  const forget = useMutation({
    mutationFn: () => api.del(`/api/instagram/accounts/${account.user_id}`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['instagram-status'] });
      client.invalidateQueries({ queryKey: ['providers'] });
    },
  });
  const limit = detail.data?.limit;
  const ask = async () => {
    const ok = await confirm({
      title: `Desconectar @${account.username || account.user_id}?`,
      description: 'O token sai do cofre e os canais que usavam esta conta voltam a pedir uma conta. Os posts já publicados continuam no Instagram.',
      confirmLabel: 'Desconectar',
      tone: 'danger',
    });
    if (ok) forget.mutate();
  };
  return (
    <li className="flex flex-col gap-1.5 rounded-lg border border-line bg-canvas p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium text-fg">@{account.username || account.user_id}</span>
        {account.account_type ? <Badge>{account.account_type === 'BUSINESS' ? 'Empresa' : 'Criador'}</Badge> : null}
        {limit ? (
          <Badge tone={limit.used >= limit.total ? 'bad' : 'neutral'} title="Posts publicados pela API nas últimas 24 h">
            {limit.used} de {limit.total} posts em 24 h
          </Badge>
        ) : null}
        <Button size="sm" variant="ghost" icon={Trash2} className="ml-auto" busy={forget.isPending} onClick={ask}>
          Desconectar
        </Button>
      </div>
      {account.expires_at ? (
        <p className="text-xs text-subtle">
          Acesso válido até {formatWhen(account.expires_at)}
          {account.expires_estimated
            ? ' (estimativa: conta 60 dias a partir de quando o token foi colado; a data real aparece depois da primeira renovação, no primeiro uso após 24 h)'
            : ''}
          . O app renova sozinho ao publicar; se passar de 60 dias sem uso, conecte de novo.
        </p>
      ) : null}
      {detail.error ? <p className="text-xs text-amber-200">Não deu para ler o limite: {String((detail.error as Error).message ?? detail.error)}</p> : null}
      <ErrorBox error={forget.error} />
    </li>
  );
};

/** Login do Instagram: abre a página da Meta e acompanha até a conta chegar (ou cola o endereço final). */
const LoginBlock: React.FC<{ status: InstagramStatus }> = ({ status }) => {
  const client = useQueryClient();
  const { toast } = useToast();
  const [login, setLogin] = useState<InstagramLinkStart | null>(null);
  const [pasted, setPasted] = useState('');
  const start = useMutation({
    mutationFn: () => api.post<InstagramLinkStart>('/api/instagram/link', {}),
    onSuccess: (data) => {
      setLogin(data);
      window.open(data.auth_url, '_blank', 'noopener');
    },
  });
  const poll = useQuery({
    queryKey: ['instagram-link', login?.state],
    queryFn: () => api.get<InstagramLinkStatus>(`/api/instagram/link/${login?.state}`),
    enabled: Boolean(login),
    refetchInterval: 2000,
  });
  const done = (username?: string) => {
    setLogin(null);
    setPasted('');
    client.invalidateQueries({ queryKey: ['instagram-status'] });
    client.invalidateQueries({ queryKey: ['providers'] });
    toast({ tone: 'good', title: `Instagram conectado${username ? `: @${username}` : ''}` });
  };
  useEffect(() => {
    if (poll.data?.status === 'done') done(poll.data.username);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [poll.data?.status]);
  const finish = useMutation({
    mutationFn: () => api.post<{ username: string }>('/api/instagram/link/code', { text: pasted, state: login?.state }),
    onSuccess: (data) => done(data.username),
  });
  const local = !status.redirect_uri || status.redirect_uri === status.default_redirect_uri;
  return (
    <div className="flex flex-col gap-2">
      <Button
        icon={ExternalLink}
        className="self-start"
        disabled={!status.app_configured}
        busy={start.isPending}
        onClick={() => start.mutate()}
      >
        Entrar com o Instagram
      </Button>
      {!status.app_configured ? (
        <p className="text-xs text-subtle">Cole o ID e o segredo do app do Instagram no cartão acima para usar o login.</p>
      ) : null}
      {login ? (
        <div className="flex flex-col gap-2 rounded-lg border border-line bg-canvas p-3 text-[13px] text-muted">
          <p>
            {poll.data?.status === 'error'
              ? `O login falhou: ${poll.data.error}`
              : local
                ? 'Aguardando o login na outra aba. Se a Meta não aceitar o endereço local, cole abaixo o endereço da página em que o login terminou.'
                : 'Depois do login, cole aqui o endereço completo da página em que ele terminou.'}
          </p>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Endereço final do login (ou o código)" className="min-w-0 flex-1">
              <Input value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder="https://…?code=…" />
            </Field>
            <Button disabled={!pasted.trim()} busy={finish.isPending} onClick={() => finish.mutate()}>
              Concluir
            </Button>
          </div>
          <ErrorBox error={finish.error} />
        </div>
      ) : null}
      <ErrorBox error={start.error} />
    </div>
  );
};

const TokenBlock: React.FC = () => {
  const client = useQueryClient();
  const { toast } = useToast();
  const [token, setToken] = useState('');
  const save = useMutation({
    mutationFn: () => api.post<InstagramAccount>('/api/instagram/token', { access_token: token }),
    onSuccess: (data) => {
      setToken('');
      client.invalidateQueries({ queryKey: ['instagram-status'] });
      client.invalidateQueries({ queryKey: ['providers'] });
      toast({ tone: 'good', title: `Instagram conectado: @${data.username}` });
    },
  });
  return (
    <form
      className="flex flex-col gap-2"
      autoComplete="off"
      onSubmit={(e) => {
        e.preventDefault();
        if (token.trim()) save.mutate();
      }}
    >
      <div className="flex flex-wrap items-end gap-2">
        <Field
          label="Ou cole um token gerado no painel da Meta"
          hint="Painel do app → Instagram → API setup with Instagram business login → Generate token. Vale 60 dias e o app renova."
          className="min-w-0 flex-1"
        >
          <Input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="IGAA…" />
        </Field>
        <Button type="submit" icon={KeyRound} disabled={!token.trim()} busy={save.isPending}>
          Conectar com token
        </Button>
      </div>
      <ErrorBox error={save.error} />
    </form>
  );
};

const OptionsBlock: React.FC<{ status: InstagramStatus }> = ({ status }) => {
  const client = useQueryClient();
  const { toast } = useToast();
  const [redirect, setRedirect] = useState(status.redirect_uri);
  const [base, setBase] = useState(status.public_base_url);
  useEffect(() => setRedirect(status.redirect_uri), [status.redirect_uri]);
  useEffect(() => setBase(status.public_base_url), [status.public_base_url]);
  const save = useMutation({
    mutationFn: () => api.put('/api/instagram/options', { redirect_uri: redirect, public_base_url: base }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['instagram-status'] });
      toast({ tone: 'good', title: 'Opções do Instagram salvas' });
    },
  });
  const dirty = redirect !== status.redirect_uri || base !== status.public_base_url;
  return (
    <form
      className="flex flex-col gap-3 border-t border-line pt-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <Field
        label="Endereço de retorno do login (opcional)"
        hint={`Cadastre o mesmo em "Valid OAuth Redirect URIs" no app. Vazio usa ${status.default_redirect_uri}; se a Meta exigir https, use outro e cole o endereço final no login.`}
      >
        <Input value={redirect} onChange={(e) => setRedirect(e.target.value)} placeholder={status.default_redirect_uri} />
      </Field>
      <Field
        label="Endereço público dos vídeos (opcional)"
        hint="O Instagram baixa o vídeo de uma URL pública. Se você sobe a pasta outputs do Vulto (só os MP4 e JPG finais) para um endereço https (ex.: um bucket), cole aqui o endereço que corresponde a ela: o app junta o caminho do vídeo dentro de outputs. Nunca suba a pasta de dados inteira: ela guarda studio.db e .master.key (o cofre com os seus tokens). Sem isso, informe a URL na hora de publicar."
      >
        <Input value={base} onChange={(e) => setBase(e.target.value)} placeholder="https://arquivos.seudominio.com/vulto-outputs" />
      </Field>
      <Button type="submit" size="sm" className="self-start" disabled={!dirty} busy={save.isPending}>
        Salvar opções
      </Button>
      <ErrorBox error={save.error} />
    </form>
  );
};

/** Seção de Configurações: contas do Instagram para publicar Reels direto pela API oficial. */
export const InstagramSection: React.FC = () => {
  const status = useInstagramStatus();
  const s = status.data;
  return (
    <div id="instagram">
      <Section
        title="Contas do Instagram"
        icon={Camera}
        description="Para publicar Reels direto pela API oficial. A conta precisa ser profissional (Empresa ou Criador de conteúdo). Com o app da Meta em modo de desenvolvimento, os posts só aparecem para quem tem papel no app."
      >
        {!s ? (
          status.error ? <ErrorBox error={status.error} /> : <p className="text-[13px] text-subtle">Carregando…</p>
        ) : (
          <>
            {s.accounts.length ? (
              <ul className="flex flex-col gap-2">
                {s.accounts.map((a) => (
                  <AccountRow key={a.user_id} account={a} />
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-muted">Nenhuma conta conectada.</p>
            )}
            <LoginBlock status={s} />
            <TokenBlock />
            <OptionsBlock status={s} />
          </>
        )}
      </Section>
    </div>
  );
};
