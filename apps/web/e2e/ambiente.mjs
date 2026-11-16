// Porta e pasta de dados dos testes de interface (compartilhadas pelo playwright.config.ts e pelo servidor).
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/** Porta própria: não briga com o app de verdade (8765) nem com a config vulto-teste (8775). */
export const E2E_PORT = Number(process.env.VS_E2E_PORT || 8786);

/** Pasta de dados temporária, apagada e recriada a cada rodada. */
export const E2E_DATA_DIR = process.env.VS_E2E_DATA_DIR || join(tmpdir(), `vulto-e2e-${E2E_PORT}`);
