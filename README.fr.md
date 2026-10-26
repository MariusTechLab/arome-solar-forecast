# Prévision solaire AROME pour Home Assistant

[![Validate](https://github.com/MariusTechLab/arome-solar-forecast/actions/workflows/validate.yml/badge.svg)](https://github.com/MariusTechLab/arome-solar-forecast/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

*[English version](README.md)*

Une prévision gratuite de production photovoltaïque pour Home Assistant,
fondée sur le **modèle AROME de Météo-France** (maille de 2,5 km) via l'API
[Open-Meteo](https://open-meteo.com/). Pas de compte, pas de clé, pas de quota
d'appels, et une mise à jour toutes les 15 minutes. Deux façons de l'utiliser :

- **l'intégration**, installée avec HACS et réglée dans l'interface. Elle
  alimente aussi la courbe de prévision du **tableau de bord Énergie** ;
- **un simple package YAML**, pour ceux qui préfèrent se passer d'intégration
  personnalisée.

Sur une installation réelle du sud de la France, la prévision AROME **faite
la veille** s'est trompée en moyenne de **17 %** sur la production du jour,
sur 17 jours d'automne. La prévision Solcast du matin s'est trompée de
**21 %**, les autres modèles météo de 20 à 31 % ([banc d'essai](docs/benchmark.md)).
Le dépôt fournit l'outil pour refaire ce test sur vos propres données.

## Fonctionnalités

- **Prévision au quart d'heure** pour aujourd'hui et demain, en kW, à partir
  du rayonnement reçu sur le plan des panneaux et de la température de l'air.
- **Modèle physique** :
  - température des cellules et coefficient de température ;
  - rendement du système et de l'onduleur ;
  - **écrêtage par l'onduleur** ;
  - **plusieurs pans de toit** (est/ouest...) ;
  - **facteurs d'ombrage par heure**, en option.
- **Capteurs prêts à l'emploi** :
  - énergie du jour, du lendemain, restante aujourd'hui, de l'heure en cours
    et de l'heure suivante ;
  - puissance maintenant, dans 30 minutes et dans une heure ;
  - pic de puissance et heure du pic, aujourd'hui et demain.
- **Courbe de prévision du tableau de bord Énergie** (intégration) : la
  production attendue s'affiche à côté de la production réelle.
- **Attribut compatible Solcast** : `detailedForecast`, avec `period_start` et
  `pv_estimate` (kW, par demi-heure), sur les capteurs du jour et du
  lendemain. Les modèles Jinja écrits pour Solcast fonctionnent en changeant
  simplement l'entité. L'action `arome_solar_forecast.get_forecast` renvoie la
  même liste.
- **Robuste** : si Open-Meteo ne répond pas, l'intégration garde la dernière
  prévision jusqu'à 6 heures. Les gros attributs de prévision ne sont pas
  enregistrés dans l'historique.
- **Contrôle quotidien de la prévision** (package) : enregistre chaque jour
  les prévisions de la veille et du matin, la production réelle et l'écart.
  Il fonctionne pour AROME et pour toute autre prévision à comparer (Solcast,
  Forecast.Solar...).
- **Banc d'essai** : classe plus de 8 modèles météo à partir de votre
  historique de production, grâce aux prévisions archivées.
- **Interface et noms d'entités** en français et en anglais.
- **Testé** : le calcul de l'intégration et chaque modèle Jinja du package
  sont comparés à une implémentation indépendante de la formule, à chaque
  modification (CI). Les validations officielles de Home Assistant (hassfest)
  et de HACS tournent aussi à chaque commit.

## Prérequis

- **Un lieu couvert par AROME** : la France et les régions voisines,
  environ de 37,5 à 55,4° N et de 12° O à 16° E. Ailleurs, choisissez un
  autre modèle (choix automatique d'Open-Meteo, ICON-D2, ICON-EU, Harmonie,
  ECMWF). Le banc d'essai indique lequel convient à votre site.
- **Intégration** : Home Assistant 2025.2 ou plus récent, et HACS.
- **Package** : Home Assistant 2024.10 ou plus récent, et Python 3.10 ou plus
  récent sur n'importe quel ordinateur pour le générateur. Le générateur est
  facultatif : le package peut aussi se modifier à la main.

## Installation avec HACS (recommandée)

1. Dans HACS, ouvrez le menu (⋮) → **Dépôts personnalisés**, puis ajoutez
   `https://github.com/MariusTechLab/arome-solar-forecast` avec le type
   **Intégration**.
2. Cherchez **AROME Solar Forecast**, téléchargez-la, puis redémarrez Home
   Assistant.
3. Allez dans **Paramètres → Appareils et services → Ajouter une intégration
   → AROME Solar Forecast**. Indiquez la puissance crête, l'inclinaison et
   l'orientation des panneaux (comme sur une boussole : 180 = sud), ainsi que
   la limite de l'onduleur. La position de la maison est proposée par défaut.
4. En option, dans le menu **Configurer** de l'intégration : deuxième et
   troisième pans de toit, rendements, coefficient de température, facteurs
   d'ombrage par heure, modèle météo, intervalle de mise à jour.
5. Pour le tableau de bord Énergie : **Paramètres → Tableaux de bord →
   Énergie → Panneaux solaires → votre capteur de production → Prévision de
   production solaire → AROME Solar Forecast**.

Vous pouvez ajouter plusieurs installations (maisons, toitures), chacune
comme une entrée séparée.

## Installation sous forme de package YAML

### 1. Générer le package

```bash
python tools/generate_package.py \
    --latitude 45.76 --longitude 4.84 \
    --array kwp=3.0,tilt=30,azimuth=180 \
    --inverter-kw 2.5 --lang fr \
    --output arome_solar_forecast.yaml
```

- **`azimuth`** est la direction des panneaux, comme sur une boussole :
  180 = sud, 90 = est, 270 = ouest. Open-Meteo et Solcast utilisent d'autres
  conventions : voir le [tableau des conventions](docs/how-it-works.md#azimuth-conventions).
- **`--array`** se répète pour chaque pan de toit, par exemple
  `--array kwp=2,tilt=20,azimuth=90 --array kwp=2,tilt=20,azimuth=270`.
- **Toutes les options** : `python tools/generate_package.py --help` et la
  [référence](#référence-des-options).

Deux décimales de latitude et de longitude (environ 1 km) suffisent, car la
maille d'AROME est de 2,5 km.

### 2. L'ajouter à Home Assistant

1. Copiez le fichier dans votre dossier de configuration, par exemple
   `config/packages/arome_solar_forecast.yaml`.
2. Activez les packages dans `configuration.yaml`, si ce n'est pas déjà fait :

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

3. Vérifiez la configuration (**Outils de développement → YAML → Vérifier la
   configuration**), puis **redémarrez** Home Assistant. La première fois, un
   redémarrage complet est nécessaire pour charger l'intégration `rest`.
4. En option, excluez de l'historique les deux capteurs techniques, qui sont
   volumineux ([examples/recorder.yaml](examples/recorder.yaml)).

Les capteurs sont disponibles moins d'une minute après le redémarrage.

### Sans Python

Copiez [packages/arome_solar_forecast.yaml](packages/arome_solar_forecast.yaml),
puis modifiez deux endroits :

- **l'URL `resource:`** : `latitude`, `longitude`, `tilt` et `azimuth`, avec
  la **convention d'Open-Meteo** (0 = sud, −90 = est, 90 = ouest) ;
- **le début du modèle `detailedForecast15`** :
  - `kwp` dans `arrays` ;
  - `ac_limit`, la limite de l'onduleur en kW, ou `none` s'il n'y en a pas ;
  - les rendements ;
  - les 24 facteurs `shading` (la position dans la liste correspond à l'heure
    locale).

## Capteurs

### Intégration

Les identifiants des entités suivent le nom de l'entrée et la langue de Home
Assistant. Avec le nom par défaut et Home Assistant en français :
`sensor.arome_solar_forecast_energie_aujourd_hui`...

| Capteur | Unité | Description |
|---|---|---|
| Énergie aujourd'hui / demain | kWh | attributs `detailedForecast` (30 min), `detailedForecast15` (15 min), `complete` |
| Énergie restante aujourd'hui | kWh | de maintenant à minuit |
| Énergie heure en cours / heure suivante | kWh | |
| Puissance maintenant / dans 30 minutes / dans 1 heure | W | statistiques à long terme |
| Pic de puissance aujourd'hui / demain | W | |
| Heure du pic aujourd'hui / demain | horodatage | |
| Dernière mise à jour | horodatage | diagnostic ; attribut `stale` si le dernier appel a échoué |

L'action `arome_solar_forecast.get_forecast` (`days`, `resolution` : `30min`
ou `15min`) renvoie `{model, updated, forecast: [{period_start, pv_estimate}]}`.

### Package YAML

Avec `--lang fr` :

| Entité | Unité | Description |
|---|---|---|
| `sensor.prevision_pv_arome_aujourd_hui` | kWh | énergie prévue aujourd'hui ; attribut `detailedForecast` |
| `sensor.prevision_pv_arome_demain` | kWh | énergie prévue demain ; attribut `detailedForecast` |
| `sensor.prevision_pv_arome_restante_aujourd_hui` | kWh | de maintenant à minuit |
| `sensor.prevision_pv_arome_puissance_maintenant` | W | puissance prévue pour le quart d'heure en cours |
| `sensor.prevision_pv_arome_puissance_dans_1_heure` | W | puissance prévue dans une heure |
| `sensor.prevision_pv_arome_pic_aujourd_hui` | W | quart d'heure le plus fort ; attribut `peak_time` |
| `sensor.prevision_pv_arome_courbe` | — | attribut `detailedForecast15` : 192 quarts d'heure |
| `sensor.prevision_pv_arome_donnees_brutes` | — | réponse d'Open-Meteo (technique) |

Sans `--lang fr`, les entités s'appellent `sensor.pv_forecast_arome_today`,
`..._tomorrow`, etc. Détails dans [le fonctionnement](docs/how-it-works.md).

## Utiliser la prévision

Les exemples ci-dessous utilisent les noms anglais par défaut.

Énergie encore attendue aujourd'hui :

```yaml
condition: numeric_state
entity_id: sensor.pv_forecast_arome_remaining_today
above: 3
```

Puissance prévue pour chaque demi-heure, comme avec Solcast :

```jinja
{% for x in state_attr('sensor.pv_forecast_arome_today', 'detailedForecast') %}
  {{ x.period_start }} : {{ x.pv_estimate }} kW
{% endfor %}
```

Exemples prêts à l'emploi :

- [examples/best_window.yaml](examples/best_window.yaml) : la meilleure heure
  de démarrage aujourd'hui pour un appareil d'une puissance et d'une durée
  données ;
- [examples/dashboard.yaml](examples/dashboard.yaml) : carte des entités,
  courbe prévision / réel, écarts quotidiens et courbe ApexCharts ;
- [examples/east_west_fr_with_check.yaml](examples/east_west_fr_with_check.yaml) :
  un package généré avec deux pans de toit, des noms en français, des facteurs
  d'ombrage et une comparaison quotidienne avec Solcast.

Une prévision n'est jamais parfaite. Dans les automatisations, prévoyez un
repli quand les capteurs sont indisponibles. Pour les décisions à court terme,
préférez les mesures en temps réel, comme l'injection sur le réseau.

## Comparer les prévisions chez vous

### En direct, à partir de maintenant

Ajoutez le contrôle quotidien à la génération du package :

```bash
python tools/generate_package.py ... \
    --actual-entity sensor.solar_energy_today \
    --compare Solcast=sensor.solcast_pv_forecast_forecast_today
```

**`--actual-entity`** est un capteur de l'énergie photovoltaïque réelle du
jour, en kWh, remis à zéro à minuit. Un `utility_meter` à cycle quotidien sur
votre compteur de production convient.

Chaque jour, le package enregistre les prévisions à 00:05 et à 06:30, puis la
production réelle et l'écart de chaque source à 23:55.

### Sur l'historique

```bash
python tools/benchmark.py --csv history.csv --entity sensor.pv_power \
    --latitude 45.76 --longitude 4.84 --array kwp=3,tilt=30,azimuth=180
```

Le fichier CSV s'obtient avec **Télécharger les données**, sur la page
Historique de Home Assistant, pour votre capteur de puissance photovoltaïque.
Voir le [guide du banc d'essai](docs/benchmark.md), en anglais.

## Référence des options

| Option | Valeur par défaut | Description |
|---|---|---|
| `--latitude`, `--longitude` | obligatoire | emplacement |
| `--array kwp=,tilt=,azimuth=` | `kwp=3,tilt=30,azimuth=180` | un par pan de toit, azimut de boussole |
| `--inverter-kw` | aucune | limite de sortie de l'onduleur en kW (écrêtage) |
| `--system-efficiency` | 0,98 | salissure, câblage, dispersion des modules |
| `--inverter-efficiency` | 0,96 | rendement de l'onduleur |
| `--temp-coefficient` | −0,004 | variation de puissance par °C au-dessus de 25 °C |
| `--cell-heating` | 0,03 | échauffement des cellules en °C par W/m² |
| `--shading` | aucun | facteurs par heure locale, par ex. `8=0.7,16=0.5,17=0.4` |
| `--model` | `meteofrance_arome_france` | tout modèle d'Open-Meteo |
| `--forecast-days` | 2 | 2 pour AROME ; jusqu'à 7 avec `meteofrance_seamless` |
| `--scan-interval` | 900 | secondes entre deux appels (600 au minimum) |
| `--lang` | `en` | noms d'entités en anglais (`en`) ou en français (`fr`) |
| `--title` | selon la langue | début du nom des entités |
| `--actual-entity` | aucune | active le contrôle quotidien |
| `--compare LABEL=entité` | aucune | autres prévisions à contrôler (option répétable) |
| `--morning-time` | `06:30:00` | heure de l'instantané du matin |
| `--output` | sortie standard | fichier à écrire |

## Questions fréquentes

**Les capteurs restent indisponibles.**
- Vérifiez que Home Assistant a bien été redémarré, et pas seulement
  rechargé, après l'ajout du premier capteur `rest`.
- Cherchez les erreurs `rest` dans le journal.
- Ouvrez l'URL `resource` dans un navigateur : elle doit renvoyer du JSON
  contenant un bloc `minutely_15`.

**La prévision est toujours trop haute.**
- Comparez la puissance crête et la limite de l'onduleur avec votre
  installation.
- Lancez le banc d'essai : le facteur d'échelle calé montre l'écart
  systématique.
- Un écart concentré le matin ou en fin d'après-midi vient d'un ombrage :
  utilisez `--shading`.

**La courbe est décalée dans le temps.** Le plus souvent, c'est le signe de
l'azimut : voir le [tableau des conventions](docs/how-it-works.md#azimuth-conventions).

**Peut-elle alimenter la prévision du tableau de bord Énergie ?** Oui, avec
l'intégration (voir l'installation). Le package YAML ne le peut pas : cette
courbe n'accepte que les intégrations.

**Intégration ou package ?** L'intégration pour un réglage dans l'interface,
le tableau de bord Énergie et la résistance aux pannes de l'API. Le package
pour une installation sans intégration personnalisée, ou pour modifier les
modèles Jinja.

**Peut-on l'utiliser hors de France ?** Oui, avec un autre modèle :
`--model best_match`, `icon_d2`, `knmi_harmonie_arome_europe`... Le banc
d'essai aide à choisir.

**Y a-t-il des prévisions P10 et P90 ?** Non, car AROME est un modèle
déterministe.

## Sources des données et conditions d'utilisation

- **Données météo** : [Open-Meteo.com](https://open-meteo.com/), sous licence
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.fr). L'API
  gratuite est réservée à un usage non commercial, dans la limite de
  10 000 appels par jour. Ce package en fait 96 par pan de toit.
- **Modèles AROME et ARPEGE** : [Météo-France](https://meteofrance.com/),
  données ouvertes sous Licence Ouverte Etalab 2.0.

Ce projet n'est affilié ni à Open-Meteo, ni à Météo-France, ni à Solcast.

## Projet associé

[Solar surplus climate control](https://github.com/MariusTechLab/solar-surplus-climate-control)
est un blueprint qui pilote une climatisation réversible sur le surplus
photovoltaïque. Son pré-refroidissement peut s'appuyer sur une prévision de
production restante, comme `sensor.pv_forecast_arome_remaining_today`.

## Développement

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python tools/validate_repo.py
```

Après une modification du générateur, régénérez le package de référence avec
la commande indiquée dans [CHANGELOG.md](CHANGELOG.md). Un test vérifie qu'il
est à jour.

## Licence

[MIT](LICENSE)
