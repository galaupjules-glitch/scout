// Vexa — fenêtre Mac native (WKWebView) qui démarre son propre serveur local
// (empaqueté dans Resources/vexa_server/) et affiche son interface, sans
// jamais ouvrir Safari/Chrome ni dépendre d'internet.
//
// Le choix et l'envoi du fichier (vidéo/photo) sont gérés CÔTÉ NATIF plutôt
// que via les mécanismes internes de WKWebView (<input type="file">,
// glisser-déposer HTML) : ceux-ci se sont révélés peu fiables dans une
// WKWebView nue. Le pont JS <-> natif (voir templates/index.html,
// window.webkit.messageHandlers) est donc la source de vérité :
//   - clic sur la case de dépôt -> NSOpenPanel natif
//   - glisser un fichier depuis le Finder sur la fenêtre -> capté nativement
//   - "Générer" -> l'upload multipart est fait en natif (URLSession),
//     directement vers /api/spoof, puis on relaie le job_id à la page pour
//     qu'elle affiche la progression comme d'habitude.
import Cocoa
import WebKit
import UniformTypeIdentifiers

let PORT = 8766
let URL_LOCALE = URL(string: "http://127.0.0.1:\(PORT)/")!
let URL_API_SPOOF = URL(string: "http://127.0.0.1:\(PORT)/api/spoof")!

// Journal persistant (~/Library/Logs/Vexa.log) : chaque étape clé du pont
// JS <-> natif y écrit une ligne, pour pouvoir diagnostiquer après coup un
// souci qu'on ne peut pas reproduire soi-même en testant à distance.
func journaliser(_ message: String) {
    let formateur = DateFormatter()
    formateur.dateFormat = "HH:mm:ss.SSS"
    let ligne = "[\(formateur.string(from: Date()))] \(message)\n"
    print(ligne, terminator: "")
    let dossierLogs = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask).first?
        .appendingPathComponent("Logs")
    guard let dossierLogs = dossierLogs else { return }
    try? FileManager.default.createDirectory(at: dossierLogs, withIntermediateDirectories: true)
    let fichierLog = dossierLogs.appendingPathComponent("Vexa.log")
    if let donnees = ligne.data(using: .utf8) {
        if let ecriture = try? FileHandle(forWritingTo: fichierLog) {
            ecriture.seekToEndOfFile()
            ecriture.write(donnees)
            try? ecriture.close()
        } else {
            try? donnees.write(to: fichierLog)
        }
    }
}

// Sous-classe de WKWebView : permet d'intercepter le glisser-déposer de
// fichiers depuis le Finder nativement (fiable), plutôt que de compter sur
// la traduction interne de WebKit vers des évènements DOM (pas fiable ici).
final class VexaWebView: WKWebView {
    weak var appDelegate: AppDelegate?

    func activerGlisserDeposer() {
        registerForDraggedTypes([.fileURL])
    }

    private func fichierDepuis(_ sender: NSDraggingInfo) -> URL? {
        let options: [NSPasteboard.ReadingOptionKey: Any] = [.urlReadingFileURLsOnly: true]
        guard let urls = sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: options) as? [URL] else {
            return nil
        }
        return urls.first
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        if fichierDepuis(sender) != nil { return .copy }
        return super.draggingEntered(sender)
    }

    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        if fichierDepuis(sender) != nil { return .copy }
        return super.draggingUpdated(sender)
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        if let fichier = fichierDepuis(sender) {
            journaliser("Glisser-déposer natif (NSDraggingDestination) reçu : \(fichier.path)")
            appDelegate?.fichierChoisiNativement(fichier)
            return true
        }
        journaliser("performDragOperation natif : pas de fichier reconnu, on délègue à WebKit")
        return super.performDragOperation(sender)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate,
                          WKDownloadDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var webView: VexaWebView!
    var serveur: Process?
    var fichierChoisi: URL?

    func applicationDidFinishLaunching(_ notification: Notification) {
        journaliser("--- Lancement de Vexa ---")
        demarrerServeur()

        let controleur = WKUserContentController()
        controleur.add(self, name: "deposerFichier")
        controleur.add(self, name: "demarrerTraitement")

        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.userContentController = controleur

        webView = VexaWebView(frame: .zero, configuration: config)
        webView.appDelegate = self
        webView.uiDelegate = self
        webView.navigationDelegate = self
        webView.activerGlisserDeposer()

        let ecran = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1200, height: 900)
        let largeur = min(1120, ecran.width - 80)
        let hauteur = min(900, ecran.height - 80)
        let rect = NSRect(x: ecran.midX - largeur / 2, y: ecran.midY - hauteur / 2, width: largeur, height: hauteur)

        window = NSWindow(contentRect: rect,
                           styleMask: [.titled, .closable, .miniaturizable, .resizable],
                           backing: .buffered, defer: false)
        window.title = "Vexa"
        window.contentView = webView
        window.setFrameAutosaveName("VexaWindow")
        window.makeKeyAndOrderFront(nil)

        chargerPageQuandPrete()
        construireMenu()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        serveur?.terminate()
    }

    // MARK: - Pont JS -> natif

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        journaliser("Message JS -> natif reçu : \(message.name) = \(message.body)")
        switch message.name {
        case "deposerFichier":
            ouvrirSelecteurFichier()
        case "demarrerTraitement":
            if let champs = message.body as? [String: Any] {
                demarrerTraitement(champs: champs.mapValues { "\($0)" })
            } else {
                journaliser("demarrerTraitement : corps du message illisible (\(type(of: message.body)))")
            }
        default:
            break
        }
    }

    private func ouvrirSelecteurFichier() {
        // Renforce la fiabilité d'affichage du panneau : s'assure que l'app
        // et sa fenêtre sont au premier plan avant de le présenter (sinon,
        // sur certains Mac, le panneau peut s'ouvrir masqué derrière une
        // autre fenêtre et donner l'impression que "rien ne se passe").
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        journaliser("Ouverture du sélecteur de fichier (NSOpenPanel)...")
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.begin { reponse in
            journaliser("NSOpenPanel fermé, réponse = \(reponse == .OK ? "OK" : "annulé")")
            if reponse == .OK, let url = panel.urls.first {
                self.fichierChoisiNativement(url)
            }
        }
    }

    // Appelé au choix via NSOpenPanel OU au glisser-déposer
    func fichierChoisiNativement(_ url: URL) {
        journaliser("Fichier choisi nativement : \(url.path)")
        fichierChoisi = url
        appelerJS("window.__fichierNatifChoisi", avec: [url.lastPathComponent])
    }

    private func demarrerTraitement(champs: [String: String]) {
        journaliser("demarrerTraitement appelé avec champs = \(champs)")
        guard let fichier = fichierChoisi else {
            journaliser("demarrerTraitement : aucun fichier natif en attente")
            appelerJS("window.__erreurNative", avec: ["Choisis d'abord un fichier."])
            return
        }
        let frontiere = "VexaBoundary\(UUID().uuidString)"
        do {
            let corps = try construireCorpsMultipart(frontiere: frontiere, champs: champs, fichier: fichier)
            var requete = URLRequest(url: URL_API_SPOOF)
            requete.httpMethod = "POST"
            requete.setValue("multipart/form-data; boundary=\(frontiere)", forHTTPHeaderField: "Content-Type")

            journaliser("Envoi natif vers \(URL_API_SPOOF.absoluteString) (\(fichier.lastPathComponent))")
            let tache = URLSession.shared.uploadTask(with: requete, fromFile: corps) { donnees, reponseHTTP, erreur in
                try? FileManager.default.removeItem(at: corps)
                DispatchQueue.main.async {
                    if let erreur = erreur {
                        journaliser("Échec upload natif : \(erreur.localizedDescription)")
                        self.appelerJS("window.__erreurNative", avec: ["Échec de l'envoi : \(erreur.localizedDescription)"])
                        return
                    }
                    let code = (reponseHTTP as? HTTPURLResponse)?.statusCode ?? -1
                    let corpsTexte = donnees.flatMap { String(data: $0, encoding: .utf8) } ?? ""
                    journaliser("Réponse upload natif : HTTP \(code) — \(corpsTexte.prefix(300))")
                    guard let donnees = donnees,
                          let json = try? JSONSerialization.jsonObject(with: donnees) as? [String: Any] else {
                        self.appelerJS("window.__erreurNative", avec: ["Réponse du serveur illisible."])
                        return
                    }
                    if let jobId = json["job_id"] as? String {
                        let total = (json["total"] as? Int) ?? 1
                        self.webView.evaluateJavaScript("window.__traitementDemarre && window.__traitementDemarre('\(jobId)', \(total))")
                    } else if let erreurMsg = json["erreur"] as? String {
                        self.appelerJS("window.__erreurNative", avec: [erreurMsg])
                    } else {
                        self.appelerJS("window.__erreurNative", avec: ["Réponse du serveur inattendue."])
                    }
                }
            }
            tache.resume()
        } catch {
            journaliser("Impossible de lire le fichier choisi : \(error.localizedDescription)")
            appelerJS("window.__erreurNative", avec: ["Impossible de lire le fichier : \(error.localizedDescription)"])
        }
    }

    private func construireCorpsMultipart(frontiere: String, champs: [String: String], fichier: URL) throws -> URL {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        FileManager.default.createFile(atPath: tmp.path, contents: nil)
        let ecriture = try FileHandle(forWritingTo: tmp)
        defer { try? ecriture.close() }

        func ecrire(_ s: String) { ecriture.write(s.data(using: .utf8)!) }

        for (cle, valeur) in champs {
            ecrire("--\(frontiere)\r\n")
            ecrire("Content-Disposition: form-data; name=\"\(cle)\"\r\n\r\n")
            ecrire("\(valeur)\r\n")
        }

        let nomFichier = fichier.lastPathComponent
        let mime = UTType(filenameExtension: fichier.pathExtension)?.preferredMIMEType ?? "application/octet-stream"
        ecrire("--\(frontiere)\r\n")
        ecrire("Content-Disposition: form-data; name=\"video\"; filename=\"\(nomFichier)\"\r\n")
        ecrire("Content-Type: \(mime)\r\n\r\n")

        let lecture = try FileHandle(forReadingFrom: fichier)
        while true {
            let morceau = lecture.readData(ofLength: 1_048_576)
            if morceau.isEmpty { break }
            ecriture.write(morceau)
        }
        try lecture.close()

        ecrire("\r\n--\(frontiere)--\r\n")
        return tmp
    }

    private func appelerJS(_ fonction: String, avec arguments: [String]) {
        guard let donnees = try? JSONSerialization.data(withJSONObject: arguments),
              let json = String(data: donnees, encoding: .utf8) else { return }
        webView.evaluateJavaScript("\(fonction) && \(fonction).apply(null, \(json))")
    }

    // MARK: - Téléchargements (Content-Disposition: attachment -> dossier
    // Téléchargements de la personne, avec gestion des doublons de nom)

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let chemin = navigationResponse.response.url?.path ?? ""
        let estTelechargement = chemin.hasPrefix("/fichier/") || chemin.hasPrefix("/telecharger-tout/")
        if estTelechargement || !navigationResponse.canShowMIMEType {
            decisionHandler(.download)
        } else {
            decisionHandler(.allow)
        }
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let dossier = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
            ?? FileManager.default.homeDirectoryForCurrentUser
        var destination = dossier.appendingPathComponent(suggestedFilename)
        var compteur = 1
        let base = destination.deletingPathExtension().lastPathComponent
        let ext = destination.pathExtension
        while FileManager.default.fileExists(atPath: destination.path) {
            let nom = ext.isEmpty ? "\(base) (\(compteur))" : "\(base) (\(compteur)).\(ext)"
            destination = dossier.appendingPathComponent(nom)
            compteur += 1
        }
        completionHandler(destination)
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        appelerJS("window.__erreurNative", avec: ["Le téléchargement a échoué : \(error.localizedDescription)"])
    }

    // MARK: - Démarrage du serveur local

    private func demarrerServeur() {
        guard let exe = Bundle.main.path(forResource: "vexa_server/Vexa", ofType: nil) else {
            afficherErreur("Le serveur Vexa est introuvable dans l'application.")
            return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: exe)
        var env = ProcessInfo.processInfo.environment
        env["VEXA_NO_BROWSER"] = "1"
        process.environment = env
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            serveur = process
        } catch {
            afficherErreur("Impossible de démarrer le serveur Vexa : \(error.localizedDescription)")
        }
    }

    private func chargerPageQuandPrete(tentative: Int = 0) {
        var requete = URLRequest(url: URL_LOCALE)
        requete.timeoutInterval = 1
        URLSession.shared.dataTask(with: requete) { _, reponse, _ in
            DispatchQueue.main.async {
                if let http = reponse as? HTTPURLResponse, (200...399).contains(http.statusCode) {
                    self.webView.load(URLRequest(url: URL_LOCALE))
                } else if tentative < 100 {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                        self.chargerPageQuandPrete(tentative: tentative + 1)
                    }
                } else {
                    self.afficherErreur("Vexa n'a pas démarré à temps. Réessaie d'ouvrir l'application.")
                }
            }
        }.resume()
    }

    private func afficherErreur(_ message: String) {
        let alerte = NSAlert()
        alerte.messageText = "Vexa"
        alerte.informativeText = message
        alerte.alertStyle = .warning
        alerte.runModal()
    }

    private func construireMenu() {
        let nom = "Vexa"
        let main = NSMenu()

        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Masquer \(nom)", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quitter \(nom)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Édition")
        edit.addItem(withTitle: "Copier", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Coller", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Tout sélectionner", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        let fenetreItem = NSMenuItem(); main.addItem(fenetreItem)
        let fenetre = NSMenu(title: "Fenêtre")
        fenetre.addItem(withTitle: "Réduire", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        fenetreItem.submenu = fenetre

        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
