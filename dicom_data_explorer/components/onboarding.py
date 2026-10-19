import reflex as rx
from dicom_data_explorer.states.ui_state import UIState


def onboarding_modal() -> rx.Component:
    """The onboarding tutorial modal."""
    current_step_data = UIState.onboarding_steps[UIState.current_onboarding_step]
    return rx.dialog.root(
        rx.dialog.content(
            rx.el.div(
                rx.el.div(
                    rx.el.div(
                        rx.icon(
                            current_step_data["icon"], class_name="text-white", size=32
                        ),
                        class_name="h-16 w-16 bg-blue-600 rounded-full flex items-center justify-center shadow-lg mb-6",
                    ),
                    class_name="flex justify-center w-full",
                ),
                rx.el.h2(
                    current_step_data["title"],
                    class_name="text-2xl font-bold text-center text-gray-900 mb-4",
                ),
                rx.el.p(
                    current_step_data["content"],
                    class_name="text-center text-gray-600 mb-8 text-lg leading-relaxed",
                ),
                rx.el.div(
                    rx.foreach(
                        UIState.onboarding_steps,
                        lambda _, i: rx.el.div(
                            class_name=rx.cond(
                                i == UIState.current_onboarding_step,
                                "w-3 h-3 rounded-full bg-blue-600 transition-all duration-300",
                                "w-2 h-2 rounded-full bg-gray-300",
                            )
                        ),
                    ),
                    class_name="flex gap-2 justify-center mb-8",
                ),
                rx.el.div(
                    rx.cond(
                        UIState.current_onboarding_step > 0,
                        rx.el.button(
                            "Previous",
                            on_click=UIState.prev_onboarding_step,
                            class_name="px-6 py-2 text-gray-600 hover:text-gray-900 font-medium transition-colors",
                        ),
                        rx.el.div(class_name="w-20"),
                    ),
                    rx.el.button(
                        rx.cond(
                            UIState.current_onboarding_step
                            == UIState.onboarding_steps.length() - 1,
                            "Got it",
                            "Next",
                        ),
                        on_click=UIState.next_onboarding_step,
                        class_name="px-8 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium shadow-md hover:shadow-lg transition-all",
                    ),
                    class_name="flex justify-between items-center w-full",
                ),
                class_name="flex flex-col items-center p-6",
            ),
            class_name="bg-white rounded-2xl shadow-2xl max-w-md w-full p-0 overflow-hidden outline-none",
        ),
        open=UIState.is_onboarding_open,
        on_open_change=UIState.close_onboarding,
    )