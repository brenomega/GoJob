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
# O edital Tech.AI identifica a campanha Blip. O blog Icatu e a 99jobs
# identificam a mesma edição. Os anúncios Gupy distinguem Petros, TIM, PRIO
# regular, PRIO verão e Opportunity. O IAG liga ao formulário da Quantum.
# Os editais identificam a vaga 4 da AgeRio e a vaga 326670 do Banco
# Central, também listada na Super Estágios.
# Visagio e Moove ligam aos anúncios de suas respectivas edições de verão.
PUBLICACOES_VERIFICADAS = {
    "blip-tech-ai-2027": {
        "fonte_empregador": "https://estagio.blip.ai",
    },
    "icatu-2027-1": {
        "fonte_legado": (
            "https://www.99jobs.com/icatu-seguros/jobs/518242-programa-de-estagio-icatu-seguros-2027-1"
        ),
        "fonte_empregador": "https://blog.icatuseguros.com.br/institucional/programa-estagio-icatu",
    },
    "petros-12891329": {
        "fonte_legado": "https://petros.gupy.io/jobs/12891329",
    },
    "tim-stem-2026": {
        "fonte_legado": "https://programadeestagiostem.gupy.io",
    },
    "prio-estagio-2027": {
        "fonte_legado": "https://programadeestagioprio.gupy.io",
    },
    "prio-summer-2027": {
        "fonte_legado": "https://prio.gupy.io/jobs/12080177",
    },
    "opportunity-summer-2027": {
        "fonte_legado": "https://opportunity.gupy.io/jobs/12463497",
        "fonte_empregador": "https://www.opportunity.com.br/summerjob/Index",
    },
    "sezzle-7541130003": {
        "fonte_outro_ats": "https://job-boards.greenhouse.io/sezzle/jobs/7541130003",
    },
    "quantum-estagio-produtos": {
        "fonte_empregador": "https://quantumfinance.com.br/processo-seletivo-estagio-produtos",
        "fonte_institucional": "https://iag.puc-rio.br/carreira",
    },
    "agerio-2026-1-4": {
        "fonte_institucional": (
            "https://www.agerio.com.br/wp-content/uploads/2025/11/"
            "EDITAL-PROCESSO-SELETIVO_ESTAGIO-2026.1-Cronograma-Modificado.pdf"
        ),
    },
    "bcb-pepp-2026-2-rj": {
        "fonte_outro_ats": (
            "https://www.superestagios.com.br/index/processoSeletivo/exibe_vagas_banco_brasil.php"
        ),
        "fonte_institucional": (
            "https://www.bcb.gov.br/content/publicacoes/publicacoes_docs/estagio/"
            "Chamada_PEPP_2026-02%20-%20prorroga%C3%A7%C3%A3o.pdf"
        ),
    },
    "visagio-summer-2027": {
        "fonte_legado": "https://visagio.gupy.io/jobs/12639446",
        "fonte_empregador": "https://www.visagio.com/vagas/summer-job2027",
    },
    "moove-summer-2027": {
        "fonte_outro_ats": "https://mappit.com.br/jobs/summer-job-moove-sao-paulo/blnup10bxl32anrt9okfyqgd0",
        "fonte_empregador": "https://summerjobmoovelub.com",
    },
}


def identidade_da_url(url):
    # Nas publicações verificadas, a identidade está no domínio e no caminho.
    return urlsplit(url)._replace(query="", fragment="").geturl().rstrip("/")


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
                "area": "TI", "geografia": "INDETERMINADA", "modalidade": "INDETERMINADA",
                "situacao": "ENCERRADA", "prazo_inscricao": "2026-03-18",
            },
            "bcb-pepp-2026-2-rj": {
                "tipo": "POS_GRADUACAO", "geografia": "RJ", "modalidade": "CONDICIONAL",
                "situacao": "ENCERRADA", "prazo_inscricao": "2026-11-13",
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

        # A listagem externa associa ao mesmo ID um endereço e uma cidade em conflito.
        evidencia_bcb = self.por_id["bcb-pepp-2026-2-rj"]["evidencia"]
        for fato in ("326670", "Av. Paulista", "Brasília"):
            self.assertIn(fato, evidencia_bcb)

    def test_rotas_apontam_para_publicacoes_da_vaga_ou_edicao(self):
        fontes_por_id = {
            linha["id"]: {
                campo: identidade_da_url(linha[campo])
                for campo in ROTAS.values() if linha[campo]
            }
            for linha in self.linhas
        }
        for id_vaga, fontes_esperadas in PUBLICACOES_VERIFICADAS.items():
            with self.subTest(id=id_vaga):
                self.assertEqual(fontes_por_id[id_vaga], fontes_esperadas)
                urls_esperadas = set(fontes_esperadas.values())
                ids_com_as_fontes = {
                    outro_id for outro_id, fontes in fontes_por_id.items()
                    if urls_esperadas.intersection(fontes.values())
                }
                self.assertEqual(
                    ids_com_as_fontes, {id_vaga}, "publicação atribuída a mais de um ID"
                )

    def test_card_da_ada_permanece_como_evidencia_complementar_da_blip(self):
        # Na revalidação de 28/11, o índice mostrou o nome do programa sem
        # confirmar o destino do card para a edição de 2027.
        blip = self.por_id["blip-tech-ai-2027"]
        links = {
            identidade_da_url(url.strip())
            for url in blip["evidencia_complementar"].split("|")
        }
        self.assertIn("https://ada.tech/talentos", links)

    def test_vagas_com_preserva_ids_e_datas_da_evidencia_complementar(self):
        revalidacao = re.search(
            r"^### Vagas\.com[^\n]*28/11/2026[^\n]*\n\n([^\n]+)",
            self.relatorio,
            re.MULTILINE,
        )
        self.assertIsNotNone(revalidacao, "faltou registrar a data da revalidação")
        self.assertIn("Famílias do legado", revalidacao.group(1))
        self.assertIn("INDETERMINADA", revalidacao.group(1))

        # A listagem datada liga cada data ao detalhe público pelo ID.
        casos = (
            ("2843164", "13/11/2026", "analista-desenvolvimento-sistemas-jr-full-stack"),
            ("2834898", "30/10/2026", "estagiario"),
        )
        urls_contadas = {
            identidade_da_url(linha[campo])
            for linha in self.publicas for campo in ROTAS.values() if linha[campo]
        }
        for id_vaga, publicacao, pagina in casos:
            with self.subTest(id=id_vaga):
                url = f"https://www.vagas.com.br/vagas/v{id_vaga}/{pagina}"
                notas = re.findall(rf"^- [^\n]*\b{id_vaga}\b[^\n]*", self.relatorio, re.MULTILINE)
                self.assertEqual(len(notas), 1, "o ID deve identificar um caso complementar")
                self.assertIn(publicacao, notas[0])
                links = re.findall(r"\]\((https://[^)]+)\)", notas[0])
                self.assertIn(url, {identidade_da_url(link) for link in links})
                self.assertNotIn(url, urls_contadas, "caso complementar entrou nas incidências")

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

        visagio = self.por_id["visagio-summer-2027"]
        self.assertEqual(visagio["area"], "TRILHA_TI")
        self.assertEqual(visagio["geografia"], "FORA_ESCOPO")
        self.assertEqual(visagio["situacao"], "ENCERRADA")
        self.assertEqual(visagio["prazo_inscricao"], "")
        self.assertEqual(
            identidade_da_url(visagio["evidencia_complementar"]),
            "https://visagio.gupy.io/jobs/12615681",
        )

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
        self.assertEqual(tabela["Famílias do legado"][0], (14, 14, 11))
        self.assertEqual(tabela["Empregador"][0], (7, 3, 2))
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
