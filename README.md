# Sport Data Solution · Projet 12 · Option B

Ce POC automatise l’étude d’une prime de mobilité et de journées bien-être. Il s’exécute sur une machine locale. Streamlit est le fichier de reporting interactif équivalent à Power BI. Python traite les classeurs et les événements, SQLite maintient l’intégrité des écritures, et un processus surveille la boîte d’entrée.

## Démarrer le reporting

Dans ce dossier avec Python 3.13, exécuter depuis un terminal Windows.

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe lancer.py
```

Ouvrir **http://127.0.0.1:8512**. Lors d’un premier lancement, 60 salariés et leurs activités sont générés à partir de données fictives. Le programme démarre la surveillance et le tableau de bord. Quitter le terminal ou utiliser Ctrl+C pour les arrêter. Dans le dossier de travail original, la base déjà préparée contient les résultats issus des classeurs de la mission. Le dossier `local/` contient l’état de travail et la clé de pseudonymisation. Il doit rester privé.

## Importer ou recharger les classeurs officiels

Utiliser les deux classeurs téléchargés depuis OpenClassrooms. Dans un environnement neuf ou dans une base qui provient déjà de ces deux fichiers, lancer la commande suivante avec leurs chemins réels.

```powershell
& .\.venv\Scripts\python.exe pipeline.py init --rh 'D:\dossier\Donnees_RH.xlsx' --sport 'D:\dossier\Donnees_sportives.xlsx' --date 2026-11-07
```

Pour garder plusieurs scénarios, attribuer un autre dossier local avec `P12_RUNTIME` avant le lancement. L’option `init` remplace la partie simulée de l’historique dans une transaction, conserve les événements ajoutés en direct et maintient les paramètres. La source de démonstration synthétique et les classeurs officiels doivent utiliser des bases distinctes. À date fixe et avec les mêmes entrées, la simulation conserve ses identifiants et son volume. Une nouvelle fenêtre d’étude utilise une nouvelle base. Un retrait de salarié bloque la recharge et demande une politique d’historisation.

## Démonstration à préparer

1. Ouvrir le tableau de bord et montrer le taux initial de 5 %, le volume d’activités et le journal. Les activités sont simulées sur une fenêtre de douze mois se terminant le 7 novembre 2026.
2. Passer le taux à 6 % et cliquer sur **Recalculer les indicateurs**. La base conserve la version précédente des paramètres. Le nombre d’activités reste stable.
3. Cliquer sur **Injecter la course**. Le profil de démonstration commence à 14 activités. L’intégration de sa première course le fait passer à 15. Le résultat gagne une activité et cinq journées bien-être.
4. Ouvrir **Notifications locales** pour afficher le message généré, puis **Monitoring** pour montrer la volumétrie, le statut et le dernier passage. La surveillance inspecte la boîte d’entrée toutes les trois secondes. Le tableau de bord se rafraîchit toutes les quatre secondes pendant une session active.

## Règles de calcul et limites

- La prime est un pourcentage du salaire brut annuel. Les déclarations actives sont Marche/Running et Vélo/Trottinette/Autres. L’indicateur compare la distance estimée à 15 km ou 25 km selon le mode et classe les autres cas actifs « à vérifier ».
- Sans clé Google, le calcul utilise une distance à vol d’oiseau entre centres de communes avec le référentiel administratif public. Cette estimation est indicative et n’établit pas un trajet domicile-travail réel. Une distance nulle peut correspondre à deux lieux de la même commune.
- La valorisation des jours est une approximation de capacité. Elle utilise le salaire brut annuel divisé par 218, multiplié par les jours attribués. Ce montant ne représente pas un coût de paie additionnel.
- Les activités historiques sont fictives. Seuls les nouveaux événements intégrés créent une notification. Les messages omettent noms, adresses, salaires et commentaires.

## Connecteurs et sécurité

Le mode Google Routes est isolé derrière `--google` et la variable `GOOGLE_MAPS_API_KEY`. Il transmet l’adresse au fournisseur, utilise WALK pour marche/course et BICYCLE comme approximation du groupe vélo/trottinette/autres. Utiliser ce mode seulement après autorisation et mise en place de la facturation. Les itinéraires piétons et cyclables peuvent manquer de chemins ou de pistes. Les tests automatisés utilisent des réponses simulées.

Le système met un message en attente par activité nouvelle dans la même transaction que l’intégration. Pour une démonstration externe autorisée avec un canal de test, configurer `SLACK_WEBHOOK_URL`, puis utiliser `pipeline.py slack --message ID` avec l’identifiant de l’activité choisi. Une tentative au statut incertain est mise à vérifier pour éviter les doublons. Le POC utilise l’aperçu local sans publier les données RH sur Slack.

Le tableau de bord est lié à l’interface locale. SQLite n’expose aucun port. Les fichiers persistants doivent rester accessibles au seul utilisateur de la machine. L’environnement de production nécessiterait un stockage chiffré, des rôles d’accès séparés et une politique de conservation.

## Fichiers

- `pipeline.py` prépare les entrées, génère les activités, valide le flux, calcule les indicateurs et gère les connecteurs.
- `app.py` affiche les mesures et permet la démonstration.
- `lancer.py` lance la surveillance et l’interface.
- `test_pipeline.py` contrôle les fenêtres, les rejets, l’idempotence, les paramètres et les deux connecteurs avec des réponses simulées.
- `communes_30.json` et `communes_34.json` contiennent le référentiel public, extrait de l’[API Découpage administratif](https://geo.api.gouv.fr/decoupage-administratif).

Documentation technique des connecteurs [Google Routes](https://developers.google.com/maps/documentation/routes/compute_route_directions) et [Slack Incoming Webhooks](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/).
