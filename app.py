"""Tableau de bord et poste de démonstration du POC Sport Data Solution."""
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

import streamlit as st

from pipeline import change_config, enqueue, metrics


def euros(cents):
    return f"{cents/100:,.2f} €".replace(",", " ").replace(".", ",")


st.set_page_config(page_title="Sport Data Solution", layout="wide", page_icon="🏃")
st.title("Sport Data Solution")
st.caption("POC local · Avantages sportifs · Activités simulées")

try:
    initial = metrics()
except Exception:
    st.error("Initialise la base depuis le terminal avec le guide de lancement.")
    st.stop()

params = initial["settings"]
anchor = date.fromisoformat(params["anchor"])
left,right = st.columns(2)
with left.expander("Paramètres de calcul",expanded=True):
    with st.form("settings"):
        rate = st.number_input("Prime sur salaire brut (%)",0.0,100.0,float(params["rate"]),0.5)
        threshold = st.number_input("Activités nécessaires",1,365,params["threshold"])
        days = st.number_input("Jours accordés",0,30,params["days"])
        if st.form_submit_button("Recalculer les indicateurs"):
            change_config(rate, threshold, days)
            st.rerun()
with right.expander("Simulation d'une nouvelle course",expanded=True):
    with st.form("race"):
        distance = st.number_input("Distance en mètres",1,500000,5000,100)
        minutes = st.number_input("Durée en minutes",1,180,32)
        if st.form_submit_button("Injecter la course"):
            start = datetime.combine(anchor,time(11))
            enqueue(dict(id="live_"+uuid4().hex,employee_id=params["demo_employee"],sport="Course à pied",
                         begins=start.isoformat(),ends=(start+timedelta(minutes=minutes)).isoformat(),
                         distance_m=distance,comment=""))
            st.success("Course déposée. Le pipeline la prend en charge au prochain passage.")


@st.fragment(run_every="4s")
def dashboard():
    state = metrics()
    k = state["kpi"]
    st.caption(f'Fenêtre de simulation se terminant le {anchor:%d/%m/%Y} · {k["employees"]} salariés · {k["practitioners"]} pratiquants déclarés')
    a,b,c,d = st.columns(4)
    a.metric("Prime indicative", euros(k["cost_cents"]))
    b.metric("Trajets potentiels",k["potential"],f'{k["review"]} à vérifier',delta_color="off")
    c.metric("Journées bien-être",k["days"],f'{k["wellness"]} bénéficiaires',delta_color="off")
    d.metric("Activités intégrées",k["activities"],f'{k["demo_count"]} pour le profil de démonstration',delta_color="off")
    st.caption(f'Taux {state["settings"]["rate"]} % · Enveloppe sur déclarations seules {euros(k["declared_cost_cents"])}'
               f' · Valorisation des jours {euros(k["days_value_cents"])} (salaire brut / 218 jours)')
    st.caption(f'Trajets confirmés par Google Routes : {k["confirmed"]}. La prime indicative combine les trajets confirmés et les estimations locales compatibles.')
    report, alerts, control = st.tabs(["Activité mensuelle","Notifications locales","Monitoring"])
    with report:
        st.bar_chart(state["trend"],x="mois",y="activités")
    with alerts:
        st.subheader("Aperçu de la file destinée à Slack")
        if not state["outbox"]:
            st.info("Injecte une course pour voir la notification générée.")
        for message in state["outbox"]:
            st.text(message["text"])
            st.caption(message["created"])
    with control:
        heart = next((row for row in state["runs"] if row["operation"]=="surveillance"),None)
        if heart and datetime.now(timezone.utc)-datetime.fromisoformat(heart["time"]) < timedelta(seconds=40):
            st.success("Pipeline actif")
        else:
            st.warning("Surveillance inactive ou en attente de démarrage")
        st.dataframe(state["runs"],hide_index=True)


dashboard()
