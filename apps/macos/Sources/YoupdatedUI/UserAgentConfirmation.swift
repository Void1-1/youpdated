import SwiftUI

extension View {
    /// Asks before Youpdated names itself in requests on an encrypted, proxied setup
    func userAgentConfirmation(isPresented: Binding<Bool>, confirm: @escaping () -> Void) -> some View {
        confirmationDialog(
            String(localized: "Send requests as Youpdated?"),
            isPresented: isPresented,
            titleVisibility: .visible
        ) {
            Button(String(localized: "Send as Youpdated"), action: confirm)
            Button(String(localized: "Cancel"), role: .cancel) {}
        } message: {
            Text(String(localized: "The config's user agent setting is \"youpdated\" which names Youpdated and its version. This setup is encrypted and uses a proxy, but that setting identifies its traffic. \"rotate\" blends in more."))
        }
    }
}
