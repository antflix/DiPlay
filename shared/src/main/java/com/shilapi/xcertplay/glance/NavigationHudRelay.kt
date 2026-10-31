package com.shilapi.xcertplay.glance

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * Best-effort Pi HUD telemetry. It never runs HTTP or JSON serialization on the CarPlay,
 * UI, or iAP2 threads. A two-second refresh also keeps long straight-road guidance alive.
 * Nothing about a failed LAN request can fail the CarPlay session.
 *
 * The destination is the owner's Pi on their isolated van network. Keep this disabled
 * for other installations unless they explicitly set up a matching receiver.
 */
object NavigationHudRelay {
    private const val TAG = "DiPlay-HudRelay"
    private const val ENDPOINT = "http://192.168.8.136:8765/navigation"
    private val started = AtomicBoolean(false)
    private val latest = AtomicReference<CarPlayGlance.Snapshot?>(null)
    private val worker = Executors.newSingleThreadScheduledExecutor { task ->
        Thread(task, "diplay-pi-navigation").apply { isDaemon = true }
    }
    private var lastFailure: String? = null

    fun install(context: Context) {
        if (!started.compareAndSet(false, true)) return
        // No song information is sent to the HUD. The listener runs on CarPlay's
        // event thread and only swaps an immutable snapshot reference.
        CarPlayGlance.hudListener = { latest.set(it) }
        latest.set(CarPlayGlance.snapshot())
        worker.scheduleWithFixedDelay({
            try {
                latest.get()?.let(::publish)
            } catch (error: Exception) {
                reportFailure(error)
            }
        }, 0, 2, TimeUnit.SECONDS)
        Log.i(TAG, "HUD relay enabled for local Pi receiver")
    }

    private fun publish(state: CarPlayGlance.Snapshot) {
        val active = state.connected && state.maneuverType != null
        val body = JSONObject().apply {
            put("connected", state.connected)
            put("navigationActive", active)
            put("maneuverType", if (active) state.maneuverType else JSONObject.NULL)
            put("drivingSide", state.drivingSide)
            put("distanceMeters", if (active) state.distanceMeters else JSONObject.NULL)
            put("road", if (active) state.road else "")
            put("arrivalEpochSeconds", state.arrivalEpochSeconds ?: JSONObject.NULL)
            put("remainingSeconds", state.remainingSeconds ?: JSONObject.NULL)
            put("remainingMeters", state.remainingMeters ?: JSONObject.NULL)
        }.toString()
        val connection = URL(ENDPOINT).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 1200
            connection.readTimeout = 1200
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connection.outputStream.use { stream ->
                stream.write(body.toByteArray(Charsets.UTF_8))
            }
            if (connection.responseCode !in 200..299) {
                throw IllegalStateException("Pi HUD HTTP " + connection.responseCode)
            }
            if (lastFailure != null) {
                Log.i(TAG, "Pi HUD connection restored")
                lastFailure = null
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun reportFailure(error: Exception) {
        val reason = error.javaClass.simpleName + ": " + (error.message ?: "")
        if (reason != lastFailure) {
            lastFailure = reason
            Log.w(TAG, "Pi HUD relay unavailable: " + reason)
        }
    }
}
