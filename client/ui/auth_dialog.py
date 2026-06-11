from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)


class AuthDialog(QDialog):
    def __init__(self, api_client, parent=None):
        super().__init__(parent)
        self.api_client = api_client
        self.username = None
        self.setWindowTitle("Writing Assistant 로그인")
        self.setFixedSize(420, 380)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 26, 28, 24)
        root.setSpacing(14)

        title = QLabel("Writing Assistant")
        title.setObjectName("authTitle")
        subtitle = QLabel("로그인 후 맞춤법 검사와 기록 저장 기능을 사용할 수 있습니다.")
        subtitle.setObjectName("authSubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        self.stack_container = QWidget()
        self.stack = QStackedLayout(self.stack_container)
        self.stack.addWidget(self._create_login_page())
        self.stack.addWidget(self._create_signup_page())
        root.addWidget(self.stack_container, 1)

        self.setStyleSheet(
            """
            QDialog {
                background: #f7efe5;
                color: #332820;
            }
            QLabel#authTitle {
                font-size: 26px;
                font-weight: 700;
            }
            QLabel#authSubtitle {
                color: #7b6658;
                font-size: 12px;
            }
            QLineEdit {
                background: #fffaf4;
                border: 1px solid #dccbbb;
                border-radius: 10px;
                padding: 9px 11px;
            }
            QPushButton {
                border: none;
                border-radius: 12px;
                padding: 10px 14px;
                font-weight: 600;
            }
            QPushButton#primaryAuthButton {
                background: #b86a3c;
                color: #fff8f2;
            }
            QPushButton#secondaryAuthButton {
                background: #e8d4bf;
                color: #3f2f26;
            }
            QCheckBox {
                spacing: 8px;
                color: #59463a;
                font-size: 12px;
            }
            """
        )

    def _create_login_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(QLabel("아이디"))
        self.login_username = QLineEdit()
        layout.addWidget(self.login_username)

        layout.addWidget(QLabel("비밀번호"))
        self.login_password = QLineEdit()
        self.login_password.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.login_password)

        self.remember_checkbox = QCheckBox("로그인 유지")
        layout.addWidget(self.remember_checkbox)

        login_btn = QPushButton("로그인")
        login_btn.setObjectName("primaryAuthButton")
        login_btn.clicked.connect(self._handle_login)
        layout.addWidget(login_btn)

        move_btn = QPushButton("회원가입")
        move_btn.setObjectName("secondaryAuthButton")
        move_btn.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        layout.addWidget(move_btn)
        layout.addStretch()

        self.login_password.returnPressed.connect(self._handle_login)
        self.login_username.setFocus()
        return page

    def _create_signup_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(QLabel("아이디"))
        self.signup_username = QLineEdit()
        layout.addWidget(self.signup_username)

        layout.addWidget(QLabel("비밀번호"))
        self.signup_password = QLineEdit()
        self.signup_password.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.signup_password)

        layout.addWidget(QLabel("비밀번호 확인"))
        self.signup_password_confirm = QLineEdit()
        self.signup_password_confirm.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.signup_password_confirm)

        signup_btn = QPushButton("가입하기")
        signup_btn.setObjectName("primaryAuthButton")
        signup_btn.clicked.connect(self._handle_signup)
        layout.addWidget(signup_btn)

        row = QHBoxLayout()
        row.addStretch()
        back_btn = QPushButton("로그인으로 돌아가기")
        back_btn.setObjectName("secondaryAuthButton")
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        row.addWidget(back_btn)
        row.addStretch()
        layout.addLayout(row)
        layout.addStretch()
        return page

    def _handle_login(self):
        username = self.login_username.text().strip()
        password = self.login_password.text().strip()
        if not username or not password:
            QMessageBox.warning(self, "입력 오류", "아이디와 비밀번호를 모두 입력해 주세요.")
            return
        try:
            self.api_client.login(username, password, self.remember_checkbox.isChecked())
            self.username = username
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "로그인 실패", str(exc))

    def _handle_signup(self):
        username = self.signup_username.text().strip()
        password = self.signup_password.text().strip()
        password_confirm = self.signup_password_confirm.text().strip()
        if not username or not password or not password_confirm:
            QMessageBox.warning(self, "입력 오류", "모든 항목을 입력해 주세요.")
            return
        if password != password_confirm:
            QMessageBox.warning(self, "입력 오류", "비밀번호가 일치하지 않습니다.")
            return
        if len(password) < 4:
            QMessageBox.warning(self, "입력 오류", "비밀번호는 4자 이상으로 입력해 주세요.")
            return
        try:
            self.api_client.signup(username, password)
            QMessageBox.information(self, "회원가입 완료", "회원가입이 완료되었습니다. 로그인해 주세요.")
            self.login_username.setText(username)
            self.login_password.clear()
            self.stack.setCurrentIndex(0)
        except Exception as exc:
            QMessageBox.critical(self, "회원가입 실패", str(exc))
