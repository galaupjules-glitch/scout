// Vexa — fenêtre Mac native (WKWebView) qui démarre son propre serveur local
// (empaqueté dans Resources/vexa_server/) et affiche son interface, sans
// jamais ouvrir Safari/Chrome ni dépendre d'internet : tout reste sur
// 127.0.0.1, sur cet ordinateur.
import Cocoa
import WebKit

let PORT = 8766
let URL_LOCALE = URL(string: "http://127.0.0.1:\(PORT)/")!

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var serveur: Process?

    func applicationDidFinishLaunching(_ notification: Notification) {
        demarrerServeur()

        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        webView = WKWebView(frame: .zero, configuration: config)
        webView.uiDelegate = self

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
