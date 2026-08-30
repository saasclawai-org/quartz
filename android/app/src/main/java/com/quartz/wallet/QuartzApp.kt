package com.quartz.wallet

import android.app.Application
import java.io.File

class QuartzApp : Application() {
    override fun onCreate() {
        super.onCreate()

        // v0.2.10: record uncaught crashes to files/last_crash.txt so the next
        // launch can show the exact stack trace (no more guessing from afar).
        val prev = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            runCatching {
                File(filesDir, "last_crash.txt").writeText(
                    buildString {
                        appendLine("time: ${java.util.Date()}")
                        appendLine("thread: ${t.name}")
                        appendLine(e.stackTraceToString())
                        e.cause?.let { c -> appendLine("cause: ${c.stackTraceToString()}") }
                    }
                )
            }
            prev?.uncaughtException(t, e)
        }
    }
}
