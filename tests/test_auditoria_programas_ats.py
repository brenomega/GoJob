"""Aceitação do corpus editorial da EAP 0.1.3, com corte em 26/11/2026."""

import csv
import re
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit


DOCS = Path(__file__).resolve().parents[1] / "docs"
CORTE = date(2026, 11, 26)
ROTAS = {
    "Famílias do legado": "fonte_legado",
    "Outros ATS e portais de seleção": "fonte_outro_ats",
    "Empregador": "fonte_empregador",
    "Institucional": "fonte_institucional",
}

# Oráculos da inspeção das ofertas. A lista identifica casos, inclusive os
# sobrepostos, para detectar trocas de rótulo que preservem os totais.
RECORTE_TI = {
    "infnet-12397235", "petros-12891329", "tim-stem-2026", "icatu-2027-1",
    "sezzle-7541130003", "blip-tech-ai-2027", "quantum-estagio-produtos",
}
VIGENTES = {
    "icatu-2027-1": ("RJ", "HIBRIDA", "2026-11-30"),
    "petros-12891329": ("RJ", "HIBRIDA", "2026-12-02"),
    "blip-tech-ai-2027": ("REMOTO_BR", "REMOTA", "2026-11-30"),
}
EDICOES_VERAO = {
    "prio-summer-2027", "opportunity-summer-2027", "btg-ferias-2027-01",
    "visagio-summer-2027", "moove-summer-2027", "napratica-6098153004",
}


class AuditoriaProgramasATS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (DOCS / "CORPUS_PROGRAMAS_ATS_2026-11-26.csv").open(
            encoding="utf-8", newline=""
        ) as arquivo:
            cls.linhas = list(csv.DictReader(arquivo, strict=True))
        cls.por_id = {linha["id"]: linha for linha in cls.linhas}
        cls.publicas = [linha for linha in cls.linhas if linha["situacao"] != "HISTORICO"]
        cls.recorte = [
            linha for linha in cls.publicas
            if linha["tipo"] in {"ESTAGIO", "PROGRAMA_ESTAGIO", "JUNIOR", "SUMMER"}
            and linha["area"] in {"TI", "TRILHA_TI"}
            and linha["geografia"] in {"RJ", "REMOTO_BR"}
        ]
        cls.vigentes = [linha for linha in cls.recorte if linha["situacao"] == "JANELA_VIGENTE"]
        cls.relatorio = (DOCS / "AUDITORIA_PROGRAMAS_ATS.md").read_text(encoding="utf-8")

    def test_corpus_datado_com_ids_unicos_e_fontes_rastreaveis(self):
        self.assertEqual(len(self.linhas), len(self.por_id), "ID repetido no CSV")
        self.assertEqual(len(self.publicas), 25)
        self.assertEqual(set(self.por_id) - {linha["id"] for linha in self.publicas}, {
            "adimax-6299518"
        })
        self.assertIn("26/11/2026", self.relatorio)
        self.assertIn("America/Sao_Paulo", self.relatorio)
        for linha in self.linhas:
            with self.subTest(id=linha["id"]):
                self.assertNotIn(None, linha, "coluna excedente")
                self.assertNotIn(None, linha.values(), "coluna ausente")
                self.assertTrue(linha["id"].strip())
                self.assertTrue(linha["evidencia"].strip())
                observado = date.fromisoformat(linha["observado_em"])
                self.assertEqual(observado, CORTE)
                fontes = [linha[campo] for campo in ROTAS.values() if linha[campo]]
                self.assertTrue(fontes)
                for fonte in fontes:
                    url = urlsplit(fonte)
                    self.assertEqual(url.scheme, "https")
                    self.assertTrue(url.hostname)
                prazo = linha["prazo_inscricao"]
                data_limite = date.fromisoformat(prazo) if prazo else None
                if linha["situacao"] == "JANELA_VIGENTE":
                    self.assertIsNotNone(data_limite)
                    self.assertGreaterEqual(data_limite, observado)

    def test_recortes_respeitam_area_geografia_e_janela_por_id(self):
        self.assertEqual({linha["id"] for linha in self.recorte}, RECORTE_TI)
        self.assertEqual(
            {linha["id"]: (linha["geografia"], linha["modalidade"], linha["prazo_inscricao"])
             for linha in self.vigentes},
            VIGENTES,
        )
        casos = {
            "norsul-12924593": {"area": "FORA_TI"},
            "adimax-6299518": {"area": "FORA_TI"},
            "agerio-2026-1-4": {
                "area": "TI", "geografia": "INDETERMINADA",
                "situacao": "ENCERRADA", "prazo_inscricao": "2026-03-18",
            },
            "gaudium-12884181": {"modalidade": "REMOTA", "geografia": "INDETERMINADA"},
            "ccee-12665402": {"modalidade": "CONFLITO", "geografia": "INDETERMINADA"},
            "tenda-estagio-2027": {"area": "TRILHA_TI", "geografia": "FORA_ESCOPO"},
            "sezzle-7541130003": {"situacao": "INDETERMINADA", "geografia": "REMOTO_BR"},
            "quantum-estagio-produtos": {"modalidade": "INDETERMINADA", "geografia": "RJ"},
        }
        for id_vaga, campos in casos.items():
            with self.subTest(id=id_vaga):
                self.assertEqual({campo: self.por_id[id_vaga][campo] for campo in campos}, campos)

    def test_verao_preserva_edicoes_controles_e_prazo_divergente(self):
        self.assertEqual(
            {linha["id"] for linha in self.publicas if linha["tipo"] == "SUMMER"},
            EDICOES_VERAO,
        )
        self.assertFalse({linha["id"] for linha in self.vigentes} & EDICOES_VERAO)
        self.assertEqual(self.por_id["prio-estagio-2027"]["tipo"], "PROGRAMA_ESTAGIO")
        for id_vaga in ("zippi-estagio-banco", "ifood-challenge-8935224002", "stone-summer-8014402003"):
            with self.subTest(id=id_vaga):
                self.assertEqual(self.por_id[id_vaga]["situacao"], "SEM_VAGA")

        opportunity = self.por_id["opportunity-summer-2027"]
        self.assertEqual(opportunity["situacao"], "ENCERRADA")
        self.assertEqual(opportunity["prazo_inscricao"], "")
        for prazo in ("23/10/2026", "27/10/2026"):
            self.assertIn(prazo, opportunity["evidencia"])

    def test_tabela_publicada_reconcilia_incidencias_de_cada_rota(self):
        tabela = {}
        denominadores = None
        for linha in self.relatorio.splitlines():
            if not linha.startswith("|"):
                continue
            nome, *colunas = [celula.strip() for celula in linha.strip("|").split("|")]
            if nome == "Rota":
                denominadores = [int(re.match(r"\d+", celula).group()) for celula in colunas]
            if nome in ROTAS:
                self.assertNotIn(nome, tabela, "rota repetida na tabela")
                tabela[nome] = [tuple(int(valor) for valor in celula.split("/")) for celula in colunas]

        recortes = (self.publicas, self.recorte, self.vigentes)
        self.assertEqual(denominadores, [len(recorte) for recorte in recortes])
        self.assertEqual(tabela.keys(), ROTAS.keys())
        for rota, colunas in tabela.items():
            self.assertEqual(len(colunas), len(recortes), rota)
        for indice, recorte in enumerate(recortes):
            ids_por_rota = {
                rota: {linha["id"] for linha in recorte if linha[ROTAS[rota]]}
                for rota in tabela
            }
            anteriores = set()
            for rota, ids in ids_por_rota.items():
                outras = set().union(*(vagas for nome, vagas in ids_por_rota.items() if nome != rota))
                with self.subTest(recorte=indice, rota=rota):
                    self.assertEqual(tabela[rota][indice],
                                     (len(ids), len(ids - anteriores), len(ids - outras)))
                anteriores.update(ids)
            self.assertEqual(anteriores, {linha["id"] for linha in recorte})

        # A edição Icatu aparece duas vezes nas rotas e a Quantum tem dois
        # links para a mesma oportunidade. A Blip amplia o recorte vigente.
        rotas_de_casos = {
            "icatu-2027-1": {"fonte_legado", "fonte_empregador"},
            "quantum-estagio-produtos": {"fonte_empregador", "fonte_institucional"},
            "blip-tech-ai-2027": {"fonte_empregador"},
        }
        for id_vaga, fontes in rotas_de_casos.items():
            with self.subTest(id=id_vaga):
                self.assertEqual({campo for campo in ROTAS.values() if self.por_id[id_vaga][campo]},
                                 fontes)
