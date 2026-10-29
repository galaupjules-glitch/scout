// Fenêtre Mac native qui affiche index.html (copié dans les ressources de l'app)
import Cocoa
import WebKit

// La barre du haut de la page sert aussi de barre de titre : on peut y déplacer la fenêtre (partie gauche, sans boutons)
final class DashboardWebView: WKWebView {
    override func mouseDown(with event: NSEvent) {
        let p = convert(event.locationInWindow, from: nil)
        let barHeight = 77 * pageZoom
        let fromTop = isFlipped ? p.y : bounds.height - p.y
        if fromTop < barHeight && p.x < bounds.width * 0.45 && !(p.x > 105 * pageZoom && p.x < 150 * pageZoom) {
            if event.clickCount == 2 { window?.zoom(nil) } else { window?.performDrag(with: event) }
            return
        }
        super.mouseDown(with: event)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate {
    var window: NSWindow!
    var webView: WKWebView!

    func applicationDidFinishLaunching(_ notification: Notification) {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()  // garde les photos et noms des modèles entre deux lancements
        webView = DashboardWebView(frame: .zero, configuration: config)
        webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground")

        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1600, height: 1000)
        window = NSWindow(contentRect: screen,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String ?? "App"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        // Barre d'outils vide : agrandit la zone de titre pour centrer les boutons rouge/jaune/vert dans la barre du haut
        window.toolbar = NSToolbar(identifier: "main")
        window.toolbarStyle = .unified
        window.backgroundColor = .black
        window.appearance = NSAppearance(named: .darkAqua)
        window.minSize = NSSize(width: 1100, height: 700)
        window.contentView = webView
        window.setFrameAutosaveName("MainWindow")
        window.makeKeyAndOrderFront(nil)

        // La page est dessinée pour un écran d'environ 2000 px de large : on dézoome pour qu'elle tienne en entier
        let saved = UserDefaults.standard.double(forKey: "zoom")
        webView.pageZoom = saved > 0 ? saved : min(1, (screen.width / 2000 * 100).rounded() / 100)

        // Vide le cache de page (disque + mémoire) à chaque lancement pour éviter d'afficher une ancienne
        // version d'index.html ; on garde le reste (localStorage) pour les photos/noms des modèles.
        let cacheTypes: Set<String> = [WKWebsiteDataTypeDiskCache, WKWebsiteDataTypeMemoryCache]
        config.websiteDataStore.removeData(ofTypes: cacheTypes, modifiedSince: .distantPast) { [weak self] in
            if let url = Bundle.main.url(forResource: "index", withExtension: "html") {
                self?.webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
            }
        }
        buildMenu()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    private func buildMenu() {
        let name = window.title
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Changer l'icône de l'app…", action: #selector(changeAppIcon), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Masquer \(name)", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quitter \(name)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Édition")
        edit.addItem(withTitle: "Copier", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Coller", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Tout sélectionner", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        let viewItem = NSMenuItem(); main.addItem(viewItem)
        let view = NSMenu(title: "Présentation")
        view.addItem(withTitle: "Recharger", action: #selector(reload), keyEquivalent: "r")
        view.addItem(withTitle: "Zoom avant", action: #selector(zoomIn), keyEquivalent: "+")
        view.addItem(withTitle: "Zoom arrière", action: #selector(zoomOut), keyEquivalent: "-")
        view.addItem(withTitle: "Taille par défaut", action: #selector(zoomReset), keyEquivalent: "0")
        view.addItem(.separator())
        view.addItem(withTitle: "Plein écran", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        viewItem.submenu = view
        NSApp.mainMenu = main
    }

    @objc func reload() { webView.reload() }

    // Choisit une photo et l'applique tout de suite comme icône de l'app (Dock, Finder, Launchpad),
    // sans avoir besoin de reconstruire l'app.
    @objc func changeAppIcon() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let url = panel.urls.first, let image = NSImage(contentsOf: url) else { return }
            NSWorkspace.shared.setIcon(image, forFile: Bundle.main.bundlePath, options: [])
            NSApp.applicationIconImage = image
        }
    }

    private func setZoom(_ z: Double) {
        webView.pageZoom = max(0.4, min(1.5, (z * 100).rounded() / 100))
        UserDefaults.standard.set(webView.pageZoom, forKey: "zoom")
    }
    @objc func zoomIn() { setZoom(webView.pageZoom + 0.05) }
    @objc func zoomOut() { setZoom(webView.pageZoom - 0.05) }
    @objc func zoomReset() {
        UserDefaults.standard.removeObject(forKey: "zoom")
        let w = NSScreen.main?.visibleFrame.width ?? 2000
        webView.pageZoom = min(1, (w / 2000 * 100).rounded() / 100)
    }

    // <input type="file"> : choix de la photo d'un modèle
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.urls : nil) }
    }

    // prompt() : renommer un modèle
    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = prompt
        alert.addButton(withTitle: "OK"); alert.addButton(withTitle: "Annuler")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        field.stringValue = defaultText ?? ""
        alert.accessoryView = field
        alert.window.initialFirstResponder = field
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn ? field.stringValue : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert(); alert.messageText = message
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
