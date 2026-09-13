//!HOOK MAIN
//!BIND HOOKED
//!DESC CapCap Preview Color Adjustments
//!SAVE PRE_LUT

float capcap_brightness = 0.0;
float capcap_contrast = 1.0;
float capcap_saturation = 1.0;
float capcap_gamma = 1.0;
float capcap_hue = 0.0;
float capcap_temp = 0.0;
float capcap_shadow_point = 0.25;
float capcap_highlight_point = 0.75;

vec4 hook() {
    vec4 color = HOOKED_tex(HOOKED_pos);
    vec3 c = color.rgb;

    // 1. Contrast: centered around 0.5
    if (abs(capcap_contrast - 1.0) > 0.0001) {
        c = (c - 0.5) * capcap_contrast + 0.5;
    }

    // 2. Brightness: additive offset
    if (abs(capcap_brightness) > 0.0001) {
        c += capcap_brightness;
    }

    // 3. Saturation: blend towards luminance
    if (abs(capcap_saturation - 1.0) > 0.0001) {
        float luma = dot(c, vec3(0.2126, 0.7152, 0.0722));
        c = mix(vec3(luma), c, capcap_saturation);
    }

    // 4. Gamma: power law curve
    if (abs(capcap_gamma - 1.0) > 0.0001) {
        c = pow(max(c, vec3(0.0)), vec3(1.0 / max(0.001, capcap_gamma)));
    }

    // 5. Hue rotation: Rodrigues rotation around grayscale diagonal (1, 1, 1) / sqrt(3)
    if (abs(capcap_hue) > 0.0001) {
        float angle = capcap_hue * 0.017453292519943295;
        float cos_a = cos(angle);
        float sin_a = sin(angle);
        vec3 k = vec3(0.5773502691896258);
        c = c * cos_a + cross(k, c) * sin_a + k * dot(k, c) * (1.0 - cos_a);
    }

    // 6. Color balance / Temperature tint
    if (abs(capcap_temp) > 0.0001) {
        vec3 tint = vec3(capcap_temp, capcap_temp * 0.2, -capcap_temp);
        c = clamp(c + tint * (1.0 - c), 0.0, 1.0);
    }

    // 7. Shadow / Highlight tone curves (piecewise linear through [0,0], [0.25, shadow], [0.75, highlight], [1,1])
    if (abs(capcap_shadow_point - 0.25) > 0.0001 || abs(capcap_highlight_point - 0.75) > 0.0001) {
        vec3 sp = vec3(capcap_shadow_point);
        vec3 hp = vec3(capcap_highlight_point);
        for (int i = 0; i < 3; i++) {
            float v = c[i];
            if (v < 0.25) {
                c[i] = v * (sp[i] / 0.25);
            } else if (v < 0.75) {
                c[i] = sp[i] + (v - 0.25) * ((hp[i] - sp[i]) / 0.5);
            } else {
                c[i] = hp[i] + (v - 0.75) * ((1.0 - hp[i]) / 0.25);
            }
        }
    }

    color.rgb = clamp(c, 0.0, 1.0);
    return color;
}

//!HOOK OUTPUT
//!BIND HOOKED
//!BIND PRE_LUT
//!DESC CapCap Preview LUT Blending

float capcap_lut_strength = 0.0;

vec4 hook() {
    if (capcap_lut_strength <= 0.0001) {
        return PRE_LUT_tex(PRE_LUT_pos);
    }
    vec4 lut_color = HOOKED_tex(HOOKED_pos);
    if (capcap_lut_strength >= 0.9999) {
        return lut_color;
    }
    vec4 pre_color = PRE_LUT_tex(PRE_LUT_pos);
    float s = clamp(capcap_lut_strength, 0.0, 1.0);
    return mix(pre_color, lut_color, s);
}

