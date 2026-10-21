/*
    kidctl-SDDM-Theme: lädt das unveränderte Breeze-Theme und zeigt PAM-Hinweise der
    kidctl-Login-Sperre (z. B. „Bildschirmzeit aufgebraucht“) statt nur „Login Failed“.
    Breeze wertet SDDM-Informationsmeldungen (informationMessage) selbst nicht aus.
*/

import QtQuick 2.15   // mit Version: SDDM mit Qt 5 (Ubuntu 24.04) braucht sie, Qt 6 akzeptiert sie

Item {
    id: wrapper

    // Meldungen des laufenden Anmeldeversuchs. SDDM schickt PAM-Hinweise (pam_echo) und am Ende
    // den allgemeinen PAM-Fehlertext („Authentication failure“) über denselben Kanal.
    property var pending: []
    property string shownInfo: ""     // wird angezeigt, bis holdTimer abläuft

    Loader {
        id: breeze
        anchors.fill: parent
        source: "file:///usr/share/sddm/themes/breeze/Main.qml"
        focus: true
    }

    Connections {
        target: sddm
        function onInformationMessage(message) {
            wrapper.pending = wrapper.pending.concat([message])
        }
        function onLoginFailed() {
            // Nur echte Hinweise anzeigen: alles außer der letzten Meldung (Fehlertext). Kommt nur
            // eine Meldung (z. B. falsches Passwort), bleibt Breezes „Login Failed“.
            var hints = wrapper.pending.slice(0, -1)
            wrapper.pending = []
            if (hints.length === 0)
                return
            wrapper.shownInfo = hints.join(" ")
            holdTimer.restart()
            // nach Breezes eigenem onLoginFailed („Login Failed“) überschreiben
            Qt.callLater(wrapper.applyInfo)
        }
        function onLoginSucceeded() {
            wrapper.pending = []
            wrapper.shownInfo = ""
        }
    }

    // Breeze löscht Meldungen nach 3 s – den Hinweis so lange erneut setzen, bis holdTimer abläuft
    Connections {
        target: breeze.item
        ignoreUnknownSignals: true
        function onNotificationMessageChanged() {
            if (wrapper.shownInfo && breeze.item.notificationMessage !== wrapper.shownInfo)
                Qt.callLater(wrapper.applyInfo)
        }
    }

    Timer {
        id: holdTimer
        interval: 10000
        onTriggered: {
            wrapper.shownInfo = ""
            if (breeze.item)
                breeze.item.notificationMessage = ""
        }
    }

    function applyInfo() {
        if (wrapper.shownInfo && breeze.item)
            breeze.item.notificationMessage = wrapper.shownInfo
    }
}
